#!python

# A python program to retrieve data from the BLE stepper motor analyzer
# probe and displaying it in realtime on the screen.

import argparse
import asyncio
import logging
import platform
import signal
import re
import sys
from tokenize import String
from turtle import width
import pyqtgraph as pg
from numpy import histogram
from pyqtgraph.Qt import QtWidgets
from bleak import BleakScanner
from capture_signal import CaptureSignal
from capture_signal_fetcher import CaptureSignalFetcher
from chart import Chart
from current_histogram import CurrentHistogram
from distance_histogram import DistanceHistogram
from filter import Filter
from probe import Probe
from probe_state import ProbeState
from time_histogram import TimeHistogram

# NOTE: Color names list here https://matplotlib.org/stable/gallery/color/named_colors.html

# Default device address. Use the flag below to override it.
# To find device addresses, run scanner_main.py and look for
# devices whose name looks like STP-XXXXXXXXXXXX.
# The device address has different format on Windows and
# on Mac OSX.


# Allows to stop the program by typing ctrl-c.
signal.signal(signal.SIGINT, lambda number, frame: sys.exit())

# Print environment info for debugging.
print(f"OS: {platform.platform()}", flush=True)
print(f"Platform:: {platform.uname()}", flush=True)
print(f"Python {sys.version}", flush=True)

parser = argparse.ArgumentParser()
parser.add_argument('--scan', dest="scan", default=False,
                    action=argparse.BooleanOptionalAction, help="If specified, scan for devices and exit.")
parser.add_argument("-d", "--device", dest="device",
                    default=None, help="The device name or address")
# The device name is an arbitrary string such as "Extruder 1".
parser.add_argument("-n", "--device_nick_name", dest="device_nick_name",
                    default=None, help="Optional nickname for the device, e.g. 'My Device'")
parser.add_argument("-m", "--max_amps", dest="max_amps",
                    type=float, default=2.0, help="Max current display.")
parser.add_argument("-u", "--units", dest="units",
                    default="steps", help="Units of movements.")
parser.add_argument("-s",  "--steps_per_unit", dest="steps_per_unit",
                    type=float, default=1.0, help="Steps per unit")
args = parser.parse_args()

MAX_AMPS = args.max_amps


amps_abs_filter = Filter(0.5)

timer_handler_counter = 0

# NOTE: Initializing pending_reset to True will reset the
# steps on program start but may display an initial spike
# with the notification or two that arrived before the
# reset. In using it, consider send a reset command before
# enabling the notifications.
pending_reset = False
pause_enabled = False

pending_direction_toggle = False

states_to_drop = 0

# Starting with default divider of 5.
capture_divider = 5
last_set_capture_divider = 0

main_event_loop = asyncio.new_event_loop()


async def scan():
    print("Scanning 5 secs for advertising BLE devices ...\n", flush=True)
    devices = await BleakScanner.discover(timeout=5)
    i = 0
    for device in devices:
        i += 1
        # print(device, flush=True)
        name = device.name or ""
        print(f"{i} device address: {device.address}  ({name})", flush=True)

if args.scan:
    asyncio.run(scan())
    sys.exit("\nScanning done.")


# Returns device address or None if not specified.
# Fatal error is specified but incorrectly.
def parse_device_flag():
    # Get flag value.
    value = args.device
    if not value:
        print(f"No user specified --device flag.")
        return None
    value = value.strip().upper()
    print(f"User specified device: [{value}]", flush=True)

    # Handle the case of a direct address. Six dual hex digit values,
    # separated by colons.
    addr_match = re.search(
        "^[0-9A-F]{2}:[0-9A-F]{2}:[0-9A-F]{2}"
        ":[0-9A-F]{2}:[0-9A-F]{2}"":[0-9A-F]{2}$", value)
    if addr_match:
        return value

    # Handle the case of a device name.
    #
    # TODO: Can we make this to work on Mac OSX where the device
    #   address has a different format and value?
    name_match = re.search(
        "^STP-([0-9A-F]{2})([0-9A-F]{2})([0-9A-F]{2})"
        "([0-9A-F]{2})([0-9A-F]{2})([0-9A-F]{2})$", value)
    if name_match:
        return (f"{name_match.group(1)}:{name_match.group(2)}"
                f":{name_match.group(3)}:{name_match.group(4)}"
                f":{name_match.group(5)}:{name_match.group(6)}")

    # Fatal error. User specified device incorrectly.
    sys.exit(
        f"Can't figure device name or address, please check --device or -d args. Aborting.")


async def select_device_address():
    print("Scanning 5 secs for advertising BLE devices ...", flush=True)
    all_devices = await BleakScanner.discover(timeout=5)
    candidates_devices = []
    for device in all_devices:
        name = device.name or ""
        if name.startswith("STP-"):
            candidates_devices.append(device)

    if len(candidates_devices) == 0:
        sys.exit("No idle STP device found.")
        return None

    if len(candidates_devices) == 1:
        print(
            f"Found a single STP device: device address: {candidates_devices[0].address}  ({name})", flush=True)
        return candidates_devices[0].address

    while True:
        print("\n-----", flush=True)
        i = 0
        for device in candidates_devices:
            i += 1
            print(f"\n{i}. {device.address} {device.name}",  flush=True)

        ok = False
        try:
            num = int(
                input(f"\nSelect device 1 to {len(candidates_devices)}, 0 abort: "))
            if num == 0:
                sys.exit("\nUser asked to abort.\n")
            if num > 0 and num <= len(candidates_devices):
                ok = True
        except ValueError:
            pass

        if ok:
            return candidates_devices[num - 1].address


# Determine device address.
device_address = parse_device_flag()

if not device_address:
    device_address = main_event_loop.run_until_complete(
        select_device_address())

print(f"Device address: [{device_address}]", flush=True)

# Co-routing. Returns Probe or None.


async def connect_to_probe():
    # device_address = args.device_address
    print(f"Units: {args.units}", flush=True)
    print(f"Steps per unit: {args.steps_per_unit}", flush=True)
    print(f"Trying to connect to device [{device_address}]...", flush=True)
    probe = await Probe.find_by_address(device_address, args.steps_per_unit)
    if not probe:
        print(f"Device not found", flush=True)
        return None
    if not await probe.connect():
        print(f"Failed to connect", flush=True)
        return None
    print(f"Connected to probe", flush=True)
    probe.info().dump()

    if probe.info().current_ticks_per_amp() == 0:
        sys.exit(f"Device reported an invalid configuration of 0 current ticks"
                 f" per Amp (hardware config {probe.info().hardware_config()}). Aborting.")

    return probe


logging.basicConfig(level=logging.INFO)
probe = main_event_loop.run_until_complete(connect_to_probe())
capture_signal_fetcher = CaptureSignalFetcher(probe)

# Here we are connected successfully to the BLE device. Start the GUI.

win_width = 1100
win_height = 700

# We set the actual size later. This is a workaround to force an
# early compaction of the buttons row.
win = pg.GraphicsLayoutWidget(show=True, size=[win_width, win_height-1])
title = f"BLE Stepper Motor Analyzer [{device_address}]"
if args.device_nick_name:
    title += f" [{args.device_nick_name}]"
win.setWindowTitle(title)
# win.resize(1100, 700)

# Layout class doc: https://doc.qt.io/qt-5/qgraphicsgridlayout.html

win.ci.layout.setColumnPreferredWidth(0, 240)
win.ci.layout.setColumnPreferredWidth(1, 240)
win.ci.layout.setColumnPreferredWidth(2, 240)
win.ci.layout.setColumnPreferredWidth(3, 240)
win.ci.layout.setColumnPreferredWidth(4, 380)


win.ci.layout.setColumnStretchFactor(0, 1)
win.ci.layout.setColumnStretchFactor(1, 1)
win.ci.layout.setColumnStretchFactor(2, 1)
win.ci.layout.setColumnStretchFactor(3, 1)
win.ci.layout.setColumnStretchFactor(4, 1)


# Graph 1 - Distance chart.
plot: pg.PlotItem = win.addPlot(name="Plot1", colspan=5)
plot.setLabel('left', 'Distance', args.units)
plot.setXRange(-10, 0)
plot.showGrid(False, True, 0.7)
plot.setAutoPan(x=True)
graph1 = Chart(plot, pg.mkPen('yellow'))

# Graph 2 - Speed chart.
win.nextRow()
plot = win.addPlot(name="Plot2", colspan=5)
plot.setLabel('left', 'Speed', f"{args.units}/s")
plot.setXRange(-10, 0)
plot.showGrid(False, True, 0.7)
plot.setAutoPan(x=True)
plot.setXLink('Plot1')  # synchronize time axis
graph2 = Chart(plot, pg.mkPen('orange'))

# Graph 3 - Current chart.
win.nextRow()
plot = win.addPlot(name="Plot3", colspan=5)
plot.setLabel('left', 'Current', 'A')
plot.setXRange(-10, 0)
plot.setYRange(0, 2)
plot.showGrid(False, True, 0.7)
plot.setAutoPan(x=True)
plot.setXLink('Plot1')  # synchronize time axis
graph3 = Chart(plot, pg.mkPen('green'))

# Graph 4 - Current histogram.
win.nextRow()
plot4 = win.addPlot(name="Plot4")
plot4.setLabel('left', 'Current', 'A')
plot4.setLabel('bottom', 'Speed', f'{args.units}/s')
plot4.setYRange(0, MAX_AMPS)
graph4 = pg.BarGraphItem(x=[], height=[],  width=0.3, brush='yellow')
plot4.addItem(graph4)

# Graph 5 - Time histogram.
plot5 = win.addPlot(name="Plot5")
plot5.setLabel('left', 'Time', '%')
plot5.setLabel('bottom', 'Speed', f"{args.units}/s")
graph5 = pg.BarGraphItem(x=[], height=[],  width=0.3, brush='salmon')
plot5.addItem(graph5)

# Graph 6 - Distance histogram.
plot6 = win.addPlot(name="Plot6")
plot6.setLabel('left', 'Distance', '%')
plot6.setLabel('bottom', 'Speed', f"{args.units}/s")
graph6 = pg.BarGraphItem(x=[], height=[],  width=0.3, brush='skyblue')
plot6.addItem(graph6)

# Graph 7 - Phase diagram.
plot7 = win.addPlot(name="Plot8")
plot7.setLabel('left', 'Coil B', 'A')
plot7.setLabel('bottom', 'Coil A', 'A')
plot7.showGrid(True, True, 0.7)
plot7.setXRange(-MAX_AMPS, MAX_AMPS)
plot7.setYRange(-MAX_AMPS, MAX_AMPS)

# Graph 8 - Capture signals.
plot8 = win.addPlot(name="Plot7")
plot8.setLabel('left', 'Current', 'A')
plot8.setLabel('bottom', 'Time', 's')
plot8.setYRange(-MAX_AMPS, MAX_AMPS)


win.nextRow()
buttons_layout = win.addLayout(colspan=5)
buttons_layout.setSpacing(20)
buttons_layout.layout.setHorizontalSpacing(30)

# Button1
button1_proxy = QtWidgets.QGraphicsProxyWidget()
button1 = QtWidgets.QPushButton('Toggle dir.')
button1_proxy.setWidget(button1)
buttons_layout.addItem(button1_proxy, row=0, col=0)

# Button2
button2_proxy = QtWidgets.QGraphicsProxyWidget()
button2 = QtWidgets.QPushButton('Reset Data')
button2_proxy.setWidget(button2)
buttons_layout.addItem(button2_proxy, row=0, col=1)

# Button3
button3_proxy = QtWidgets.QGraphicsProxyWidget()
button3 = QtWidgets.QPushButton(f'Time Scale X{capture_divider}')
button3_proxy.setWidget(button3)
buttons_layout.addItem(button3_proxy, row=0, col=2)

# Button4
button4_proxy = QtWidgets.QGraphicsProxyWidget()
button4 = QtWidgets.QPushButton('Pause')
button4_proxy.setWidget(button4)
buttons_layout.addItem(button4_proxy, row=0, col=3)


# This is a hack to force the view compacting the buttons
# row ASAP. We created win with similar but slightly different
# size for this to work.
win.resize(win_width, win_height)
# win.show()

# We cache the last reported state so we can compute speed.
last_state = None

# Number of state updates so far.
updates_counter = 0


def update_from_state(state: ProbeState):
    global probe, graph1, graph2, last_state, updates_counter, pause_enabled, states_to_drop

    if updates_counter % 100 == 0:
        print(f"{updates_counter:06d}: {state}", flush=True)
    updates_counter += 1

    if last_state is None:
        print(f"No last state", flush=True)
        speed = 0
    else:
        delta_t = state.timestamp_secs - last_state.timestamp_secs
        # Normal intervals are 0.020. If it's larger, we are missing
        # notification packets.
        if delta_t > 0.025:
            print(f"Data loss: {delta_t*1000:3.0f} ms", flush=True)
        if delta_t <= 0:
            # Notification is too fast, no change in timestamp. We
            # want to avoid divide by zero.
            # last_state = state
            print(f"Duplicate notifcation TS", flush=True)
            return
        speed = (state.steps - last_state.steps) / delta_t

    amps_abs_filter.add(state.amps_abs)

    if not pause_enabled and not pending_reset and states_to_drop <= 0:
        # Distance
        graph1.add_point(state.timestamp_secs,
                         state.steps / args.steps_per_unit)
        # print(f"** point {state.steps / args.steps_per_unit:.3}", flush=True)
        # Speed
        graph2.add_point(state.timestamp_secs, speed / args.steps_per_unit)
        # current
        graph3.add_point(state.timestamp_secs, amps_abs_filter.value())

    if states_to_drop > 0:
        # print(f"*** droping state", flush=True)
        states_to_drop -= 1

    last_state = state


def on_reset_button():
    global pending_reset
    pending_reset = True


def on_pause_button():
    global pause_enabled, button4
    if pause_enabled:
        button4.setText("Pause")
        pause_enabled = False
    else:
        button4.setText("Continue")
        pause_enabled = True


def on_scale_button():
    global capture_divider, last_set_capture_divider
    if capture_divider == 1:
        capture_divider = 2
    elif capture_divider == 2:
        capture_divider = 5
    elif capture_divider == 5:
        capture_divider = 10
    elif capture_divider == 10:
        capture_divider = 20
    else:
        capture_divider = 1
    button3.setText(f"Time Scale X{capture_divider}")


def on_direction_button():
    global pending_direction_toggle
    pending_direction_toggle = True


async def do_nothing():
    None


def timer_handler():
    global probe, timer_handler_counter, slot_cycle, graph1, graph2, graph3, graph4, graph5, graph6, plot8
    global capture_signal_fetcher, pending_reset, pause_enabled
    global buttons_layout, last_state, states_to_drop
    global capture_divider, last_set_capture_divider, pending_direction_toggle
    global main_event_loop

    # Process any pending events from background notifications.
    main_event_loop.run_until_complete(do_nothing())

    if pending_reset:
        main_event_loop.run_until_complete(probe.write_command_reset_data())

        # Drop next three states to clear the pipe.
        states_to_drop = 3

        graph1.clear()
        graph2.clear()
        graph3.clear()

        graph4.setOpts(x=[], height=[])
        graph5.setOpts(x=[], height=[])
        graph6.setOpts(x=[], height=[])

        plot8.clear()
        capture_signal_fetcher.reset()
        pending_reset = False

    if pending_direction_toggle:
        main_event_loop.run_until_complete(
            probe.write_command_toggle_direction())
        pending_direction_toggle = False

    if capture_divider != last_set_capture_divider:
        main_event_loop.run_until_complete(
            probe.write_command_set_capture_divider(capture_divider))
        last_set_capture_divider = capture_divider
        print(f"Capture divider set to {last_set_capture_divider}", flush=True)

    updates_enabled = not pause_enabled

    slot = timer_handler_counter % 25

    # Once in a while update the histograms.
    if updates_enabled and slot == 14:
        histogram: CurrentHistogram = main_event_loop.run_until_complete(
            probe.read_current_histogram())
        graph4.setOpts(x=histogram.centers(), height=histogram.heights(
        ), width=0.75*histogram.bucket_width())
    elif updates_enabled and slot == 5:
        histogram: TimeHistogram = main_event_loop.run_until_complete(
            probe.read_time_histogram())
        graph5.setOpts(x=histogram.centers(), height=histogram.heights(
        ), width=0.75*histogram.bucket_width())
    elif updates_enabled and slot == 10:
        histogram: DistanceHistogram = main_event_loop.run_until_complete(
            probe.read_distance_histogram())
        graph6.setOpts(x=histogram.centers(), height=histogram.heights(
        ), width=0.75*histogram.bucket_width())
    elif updates_enabled and slot in [16,  18, 20, 22, 24]:
        capture_signal: CaptureSignal = main_event_loop.run_until_complete(
            capture_signal_fetcher.loop())
        if capture_signal:
            plot8.clear()
            plot8.plot(capture_signal.times_sec(),
                       capture_signal.amps_a(), pen='yellow')
            plot8.plot(capture_signal.times_sec(),
                       capture_signal.amps_b(), pen='skyblue')

            plot7.clear()
            plot7.plot(capture_signal.amps_a(),
                       capture_signal.amps_b(), pen='greenyellow')

    timer_handler_counter += 1


button1.clicked.connect(lambda: on_direction_button())
button2.clicked.connect(lambda: on_reset_button())
button3.clicked.connect(lambda: on_scale_button())
button4.clicked.connect(lambda: on_pause_button())


# Receives the state updates from the device.
def callback_handler(probe_state: ProbeState):
    update_from_state(probe_state)


# NOTE: The notification system keeps a reference to the  event
# loop which is main_event_loop and uses it to post events.
# Running the event loop periodically in the timer handler
# below services these events.
main_event_loop.run_until_complete(
    probe.set_state_notifications(callback_handler))

timer = pg.QtCore.QTimer()
timer.timeout.connect(timer_handler)
# Interval in ms.
timer.start(20)

if __name__ == '__main__':
    pg.exec()
