
#include "settings/nvs_config.h"

#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "io/io.h"
#include "nvs.h"
#include "nvs_flash.h"
#include <memory.h>
#include <rom/crc.h>
#include <stdio.h>

namespace nvs_config {

static constexpr auto TAG = "nvs_config";

static constexpr auto kStorageNamespace = "settings";

bool read_acquisition_settings(analyzer::Settings* settings) {
  // Open
  nvs_handle_t my_handle = -1;
  bool need_to_close = false;
  esp_err_t err = nvs_open(kStorageNamespace, NVS_READONLY, &my_handle);
  if (err != ESP_OK) {
    ESP_LOGW(TAG, "read_settings() failed to open nvs: %04x", err);
  } else {
    need_to_close = true;
  }

  // Read offset1.
  int16_t offset1;
  if (err == ESP_OK) {
    err = nvs_get_i16(my_handle, "offset1", &offset1);
    if (err != ESP_OK) {
      ESP_LOGW(TAG, "read_settings() failed read offset1: %04x", err);
    }
  }

  // Read offset 2.
  int16_t offset2;
  if (err == ESP_OK) {
    err = nvs_get_i16(my_handle, "offset2", &offset2);
    if (err != ESP_OK) {
      ESP_LOGW(TAG, "read_settings() failed read offset2: %04x", err);
    }
  }

  // Read is_reverse flag.
  uint8_t is_reverse_direction;
  if (err == ESP_OK) {
    err = nvs_get_u8(my_handle, "is_reverse", &is_reverse_direction);
    if (err != ESP_OK) {
      ESP_LOGW(TAG, "read_settings() failed read is_reverse: %04x", err);
    }
  }

  // Close.
  if (need_to_close) {
    nvs_close(my_handle);
  }

  // Handle results.
  if (err != ESP_OK) {
    return false;
  }
  settings->offset1 = offset1;
  settings->offset2 = offset2;
  settings->is_reverse_direction = (bool)is_reverse_direction;
  return true;
}

bool write_acquisition_settings(const analyzer::Settings& settings) {
  // Open
  nvs_handle_t my_handle = -1;
  bool need_to_close = false;
  esp_err_t err = nvs_open(kStorageNamespace, NVS_READWRITE, &my_handle);
  if (err != ESP_OK) {
    ESP_LOGE(TAG, "write_settings() failed to open nvs: %04x", err);
  } else {
    need_to_close = true;
  }

  // TODO: What is the recomanded way to avoid crashing while
  // writing to flash while interrupts are active? Currently we use
  // taskDISABLE_INTERRUPTS, which disables only on the current
  // core, and configure menuconfig for a single core FreeRTOS.
  // 
  // Interrupts are disabled here for up to 10ms as measured
  // on osciloscope.

  // Write offset1.
  if (err == ESP_OK) {
    taskDISABLE_INTERRUPTS();
    err = nvs_set_i16(my_handle, "offset1", settings.offset1);
    taskENABLE_INTERRUPTS();
    if (err != ESP_OK) {
      ESP_LOGE(TAG, "write_settings() failed to write offset1: %04x", err);
    }
  }

  // Write offset2.
  if (err == ESP_OK) {
    taskDISABLE_INTERRUPTS();
    err = nvs_set_i16(my_handle, "offset2", settings.offset2);
    taskENABLE_INTERRUPTS();
    if (err != ESP_OK) {
      ESP_LOGE(TAG, "write_settings() failed to write offset2: %04x", err);
    }
  }

  // Write is_reverse flag.
  if (err == ESP_OK) {
    taskDISABLE_INTERRUPTS();
    err = nvs_set_u8(
        my_handle, "is_reverse", settings.is_reverse_direction ? 0 : 1);
    taskENABLE_INTERRUPTS();
    if (err != ESP_OK) {
      ESP_LOGE(TAG, "write_settings() failed to write is_reverse: %04x", err);
    }
  }

  // Commit updates.
  if (err == ESP_OK) {
    io::TEST2.set();
    taskDISABLE_INTERRUPTS();
    err = nvs_commit(my_handle);
    taskENABLE_INTERRUPTS();
    io::TEST2.clr();
    if (err != ESP_OK) {
      ESP_LOGE(TAG, "write_settings() failed to commit: %04x", err);
    }
  }

  // Close.
  if (need_to_close) {
    nvs_close(my_handle);
  }
  return err == ESP_OK;
}

}  // namespace nvs_config