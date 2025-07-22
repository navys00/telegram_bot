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

// Функция для обрезки изображения до 200x200
async function cropTo200x200(buffer) {
  
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

    let cropX = 0, cropY = 0, cropWidth = 600, cropHeight = 600;

    if (width > 200) {
      cropX = Math.floor((width - 200) / 2);
    } else {
      cropWidth = width;
    }
      
    if (height > 200) {
      cropY = Math.floor((height - 200) / 2);
    } else {
      cropHeight = height;
    }
      
    const croppedImage = image.crop({x:cropX,y: cropY,w: cropWidth,h: cropHeight});
      
    if (cropWidth < 200 || cropHeight < 200) {
      croppedImage.resize(200, 200, Jimp.RESIZE_NEAREST_NEIGHBOR);
    }


    return await croppedImage.getBuffer(JimpMime.jpeg)
  } catch (err) {
    console.error("❌ Ошибка обработки изображения:", err);
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
    return await image.greyscale()
      .contrast(1)
      .brightness(1.2)
      .getBuffer(JimpMime.jpeg);
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
            // const croppedBuffer = await cropTo200x200(buffer);
            const croppedFilename = `${downloadDir}/cropped_${Date.now()}.jpg`;
            fs.writeFileSync(croppedFilename, buffer);

            // Предобработка изображения
            console.log("🖼️ Начинаем предобработку изображения...");
            const processedBuffer = await preprocessImage(buffer);
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
                  tessedit_char_whitelist: 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyzАБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯабвгдеёжзийклмнопрстуфхцчшщъыьэюя0123456789.,-—()!?'
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
              // await client.sendMessage("me", {
              //   message: `🔔 Новый пост в "${channel.title}"\n\n📄 Распознанный текст:\n${cleanedText}\n\n✂️ Обработана область 200x200 пикселей`,
              //   file: processedFilename
              // });
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



// const fs = require("fs");
// const {Jimp} = require("jimp");
// async function f(){

// const path = "./downloads/photo_1752238914535.jpg";
// const buffer = fs.readFileSync(path);
// // const image = await Jimp.fromBuffer(buffer);
// const image = await Jimp.read("./downloads/photo_1752238914535.jpg");

// // const buffer = await fs.readFile("photo_1752238914536.png");
// // const image =await Jimp.read("photo_1752238914536.png");
// // image.greyscale()
// }


// f()
