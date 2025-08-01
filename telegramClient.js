require('dotenv').config();
const { TelegramClient } = require("telegram");
const { NewMessage } = require("telegram/events");
const { StringSession } = require("telegram/sessions");
const Tesseract = require('tesseract.js');
const input = require("input");
const fs = require("fs");
const { Jimp, JimpMime } = require("jimp");
const fileType = require('file-type');

const apiId = parseInt(process.env.API_ID);
const apiHash = process.env.API_HASH;
const stringSession = new StringSession(process.env.SESSION);



async function cropFromTop(buffer, topMargin = 0,rightMargin = 0) {
  try {
    if (!buffer || !Buffer.isBuffer(buffer)) {
      throw new Error("Буфер изображения некорректный!");
    }

    const type = await fileType.fileTypeFromBuffer(buffer);
    if (!type || !type.mime.startsWith('image/')) {
      throw new Error(`❌ Неподдерживаемый MIME тип: ${type?.mime || 'неизвестно'}`);
    }

    const image = await Jimp.read(buffer);
    const { width, height } = image.bitmap;

    // Проверка: отступ должен быть числом и не отрицательным
    if (typeof topMargin !== 'number' || topMargin < 0) {
      throw new Error("Отступ должен быть неотрицательным числом");
    }

    // Ограничиваем отступ высотой изображения
    const safeMargin = Math.min(topMargin, height - 1); // чтобы высота обрезанного изображения > 0
    const safeRightMargin = Math.min(rightMargin, width - 1)
    const cropWidth = width - safeRightMargin;
    const cropY = safeMargin;
    const cropHeight = height -950;

    if (cropHeight <= 0) {
      throw new Error("Отступ превышает высоту изображения");
    }

    // Обрезаем изображение
    const croppedImage = image.crop({
      x: 0,
      y: cropY,
      w: cropWidth,
      h: cropHeight
    });

    // Сохраняем обрезанное изображение в буфер
    return await croppedImage.getBuffer(JimpMime.png);
  } catch (err) {
    console.error("❌ Ошибка при обрезке изображения с отступом:", err);
    throw err;
  }
}
// Предобработка изображения
async function preprocessImage(buffer) {
  try {
    if (!buffer || !Buffer.isBuffer(buffer)) {
      throw new Error("Буфер изображения некорректный!");
    }

    const type = await fileType.fileTypeFromBuffer(buffer);
    if (!type || !type.mime.startsWith('image/')) {
      throw new Error(`❌ Неподдерживаемый MIME тип: ${type?.mime || 'неизвестно'}`);
    }

    const image = await Jimp.read(buffer);
    let tmp_mime=''
    if(image.hasAlpha()){
      tmp_mime=JimpMime.png
    } 
    else{
      tmp_mime=JimpMime.jpeg
    }
    return await image
    .greyscale()
      .brightness(1)
      .color([                          // Коррекция зелёного
      { apply: 'green', params: [0.3] }
    ])
    
      // .threshold({ max: 120 })
      .getBuffer(tmp_mime);
  } catch (err) {
    console.error("❌ Ошибка предобработки:", err);
    throw err;
  }
}

// Основной клиент
(async () => {
  const client = new TelegramClient(stringSession, apiId, apiHash, {
    connectionRetries: 5,
  });

  await client.start({
    phoneNumber: async () => await input.text("Введите номер телефона: "),
    phoneCode: async () => await input.text("Введите код из Telegram: "),
  });

  console.log("Вы успешно подключены!");

  try {
    const channel = await client.getEntity("primerABCD");

    client.addEventHandler(
      async (event) => {
        const msg = event.message;
        console.log("Новый пост или сообщение в канале:", msg.message || '<media or unnamed>');

        if (msg.photo) {
          try {
            const downloadDir = "./downloads";
            if (!fs.existsSync(downloadDir)) fs.mkdirSync(downloadDir);

            const buffer = await client.downloadMedia(msg);
            if (!buffer || !Buffer.isBuffer(buffer) || buffer.length < 100) {
              throw new Error("❌ Скачанное изображение некорректное!");
            }

            const type = await fileType.fileTypeFromBuffer(buffer);
            if (!type || !type.mime.startsWith('image/')) {
              throw new Error(`❌ Неподдерживаемый MIME тип: ${type?.mime || 'неизвестно'}`);
            }

            // Сохраняем оригинальное изображение
            const filename = `${downloadDir}/photo_${Date.now()}.${type.ext}`;
            fs.writeFileSync(filename, buffer);

            // Обрезаем изображение
            console.log("✂️ Начинаем обрезку до 200x200...");
            const croppedBuffer = await cropFromTop(buffer,310,120); //можно оставить 80, чтобы видеть счет
            // const croppedFilename = `${downloadDir}/cropped_${Date.now()}.jpg`;
            // fs.writeFileSync(croppedFilename, buffer);

            // Предобработка изображения
            console.log("🖼️ Начинаем предобработку изображения...");
            const processedBuffer = await preprocessImage(croppedBuffer);
            const processedFilename = `${downloadDir}/processed_${Date.now()}.jpg`;
            fs.writeFileSync(processedFilename, processedBuffer);

            // Распознавание текста
            console.log("📄 Начинаем OCR...");
            const result = await Tesseract.recognize(
              processedBuffer,
              'eng+rus',
              {
                config: {
                  psm: 6,
                  oem: 1,
                  tessedit_char_whitelist: 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyzАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя0123456789.,-—:;()"\'«»…()!?'
                }
              }
            );

            const rawText = result?.data?.text || '';
            const cleanedText = rawText
              .replace(/[^\w\s.,?!—\u0400-\u04FF]/g, '')
              .replace(/\s+/g, ' ')
              .trim();

            // Отправляем результат
            if (cleanedText.length > 0) {
              console.log(cleanedText)
            } else {
              console.log("⚠️ Текст не найден на изображении");
              await client.sendMessage("me", {
                message: `🔔 В "${channel.title}" найдено изображение, но текст не распознан.`,
                file: processedFilename
              });
            }

          } catch (err) {
            console.error("❌ Ошибка при обработке изображения:", err);
          }
        }

        // Пересылка медиа и текста
        if (msg.media && !msg.photo) {
          await client.forwardMessages("me", {
            messages: [msg],
            fromPeer: channel
          });
        } else if (!msg.photo) {
          await client.sendMessage("me", {
            message: `🔔 Новый пост в "${channel.title}":\n\n${msg.message}`
          });
        }
      },
      new NewMessage({ chats: [channel.id] })
    );

  } catch (err) {
    console.error("❌ Ошибка работы с каналом:", err);
  }

})();

