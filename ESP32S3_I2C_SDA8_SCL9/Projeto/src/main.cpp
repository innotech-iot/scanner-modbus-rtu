#include <Arduino.h>
#include <Wire.h>
#include <driver/gpio.h>
#include <esp_log.h>

#ifndef I2C_DEFAULT_SDA
#define I2C_DEFAULT_SDA -1
#endif
#ifndef I2C_DEFAULT_SCL
#define I2C_DEFAULT_SCL -1
#endif

static int sdaPin = -1;
static int sclPin = -1;
static bool busReady = false;
static bool automatic = false;
static uint32_t lastScan = 0;
static String input;
static bool lineOverflow = false;
constexpr uint32_t kFrequency = 100000;
constexpr uint32_t kInterval = 5000;

static bool safePin(int pin) {
  // Do not touch flash/PSRAM, console UART, native USB or strapping pins.
  return pin >= 0 && pin <= 48 && GPIO_IS_VALID_OUTPUT_GPIO(pin) &&
         !(pin >= 26 && pin <= 37) && pin != 19 && pin != 20 &&
         pin != 43 && pin != 44 && pin != 0 && pin != 3 &&
         pin != 45 && pin != 46;
}

static void help() {
  Serial.println("\nINNOTECH | Scanner I2C ESP32-S3");
  Serial.println("Serial: 115200 baud, 8N1. Comandos terminam com Enter.");
  Serial.println("PINS <SDA> <SCL> : define GPIOs, busca e repete a cada 5 s");
  Serial.println("SCAN             : faz uma busca agora");
  Serial.println("AUTO ON / OFF    : liga/desliga repeticao");
  Serial.println("STATUS           : mostra configuracao");
  Serial.println("HELP             : mostra esta ajuda");
  Serial.println("Pinos nao sao adivinhados nem persistidos apos reiniciar.");
}

static void scanBus() {
  lastScan = millis();
  if (!busReady) {
    Serial.println("SEM PINOS: informe PINS <GPIO_SDA> <GPIO_SCL>.");
    return;
  }
  if (digitalRead(sdaPin) == LOW || digitalRead(sclPin) == LOW) {
    Serial.println("BUS_OCUPADO: SDA ou SCL esta em nivel baixo; busca cancelada.");
    return;
  }
  Serial.printf("\nSCAN_BEGIN SDA=%d SCL=%d FREQ=%lu HZ\n", sdaPin, sclPin,
                static_cast<unsigned long>(kFrequency));
  unsigned found = 0;
  unsigned errors = 0;
  for (uint8_t address = 0x08; address <= 0x77; ++address) {
    Wire.beginTransmission(address);
    // Address probe only: no register address or data byte is sent.
    uint8_t result = Wire.endTransmission(true);
    if (result == 0) {
      ++found;
      Serial.printf("FOUND 0x%02X  decimal=%u  endereco de 7 bits\n", address, address);
    } else if (result != 2) {
      ++errors;
      Serial.printf("ERRO 0x%02X codigo=%u\n", address, result);
      if (digitalRead(sdaPin) == LOW || digitalRead(sclPin) == LOW) {
        Serial.println("BUS_OCUPADO: interrompendo busca; resultado parcial.");
        Serial.printf("SCAN_ABORTED encontrados=%u erros=%u\n", found, errors);
        return;
      }
    }
    delay(1);
  }
  if (found == 0) Serial.println("Nenhum endereco respondeu com ACK nesta busca.");
  Serial.printf("SCAN_END encontrados=%u erros=%u\n", found, errors);
}

static void configurePins(int sda, int scl) {
  if (!safePin(sda) || !safePin(scl) || sda == scl) {
    Serial.println("PINOS_INVALIDOS: use GPIOs distintos, livres e ligados ao I2C.");
    Serial.println("Nao permite flash/PSRAM 26..37, USB 19/20, UART 43/44 ou strapping 0/3/45/46.");
    return;
  }
  automatic = false;
  if (busReady) Wire.end();
  busReady = false;
  if (!Wire.begin(sda, scl, kFrequency)) {
    Serial.println("ERRO: nao foi possivel iniciar o controlador I2C.");
    return;
  }
  Wire.setTimeOut(25);
  sdaPin = sda; sclPin = scl; busReady = true; automatic = true;
  Serial.printf("PINOS_OK SDA=%d SCL=%d | I2C 100 kHz | timeout 25 ms\n", sdaPin, sclPin);
  scanBus();
}

static void command(String line) {
  line.trim(); line.toUpperCase();
  if (!line.length()) return;
  if (line == "HELP") help();
  else if (line == "SCAN") scanBus();
  else if (line == "STATUS")
    Serial.printf("STATUS pronto=%d SDA=%d SCL=%d frequencia=%lu auto=%d\n",
                  busReady, sdaPin, sclPin, static_cast<unsigned long>(kFrequency), automatic);
  else if (line == "AUTO OFF") { automatic = false; Serial.println("AUTO_OFF"); }
  else if (line == "AUTO ON") {
    if (!busReady) Serial.println("SEM PINOS: configure PINS antes de AUTO ON.");
    else { automatic = true; scanBus(); }
  } else if (line.startsWith("PINS ")) {
    int sda, scl; char extra;
    if (sscanf(line.c_str(), "PINS %d %d %c", &sda, &scl, &extra) == 2) configurePins(sda, scl);
    else Serial.println("SINTAXE: PINS <GPIO_SDA> <GPIO_SCL>");
  } else Serial.println("Comando desconhecido. Envie HELP.");
}

void setup() {
  Serial.begin(115200);
  delay(700);
  esp_log_level_set("i2c", ESP_LOG_NONE);
  input.reserve(96);
  help();
  if (I2C_DEFAULT_SDA >= 0 && I2C_DEFAULT_SCL >= 0)
    configurePins(I2C_DEFAULT_SDA, I2C_DEFAULT_SCL);
  else Serial.println("AGUARDANDO_PINOS: nenhum GPIO I2C foi ativado.");
}

void loop() {
  while (Serial.available()) {
    char c = static_cast<char>(Serial.read());
    if (c == '\n' || c == '\r') {
      if (lineOverflow) Serial.println("Comando muito longo; descartado.");
      else command(input);
      input = ""; lineOverflow = false;
    } else if (!lineOverflow) {
      if (input.length() < 95) input += c;
      else { input = ""; lineOverflow = true; }
    }
  }
  if (automatic && busReady && static_cast<uint32_t>(millis() - lastScan) >= kInterval) scanBus();
  delay(5);
}
