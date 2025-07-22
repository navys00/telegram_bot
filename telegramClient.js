require('dotenv').config();
const { TelegramClient } = require("telegram");
const { NewMessage } = require("telegram/events");
const { StringSession } = require("telegram/sessions");
const Tesseract = require('tesseract.js');
const input = require("input");
const fs = require("fs");
const {Jimp, JimpMime }= require("jimp");
const fileType = require('file-type');

const apiId = parseInt(process.env.API_ID);
const apiHash = process.env.API_HASH;
const stringSession = new StringSession(process.env.SESSION);

// Функция для выделения области 200x200 пикселей из изображения
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
    // console.log(image.bitmap.width,image.bitmap.height)
    // Вычисляем координаты для обрезки центральной области 200x200
    let cropX = 0;
    let cropY = 0;
    let cropWidth = 200;
    let cropHeight = 200;

    // Если изображение больше 200x200, обрезаем центральную область
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

    // Обрезаем изображение
    const croppedImage = image.crop(cropX, cropY, cropWidth, cropHeight);

    // Если обрезанное изображение меньше 200x200, увеличиваем его
    if (cropWidth < 200 || cropHeight < 200) {
      croppedImage.resize(200, 200,);
    }
    const processedBuffer = await croppedImage.getBuffer(JimpMime.jpeg)
    // return new Promise((resolve, reject) => {
    //   croppedImage
    //     .getBuffer('image/jpeg')
    //     .then(croppedBuffer => resolve(croppedBuffer))
    //     .catch(err => reject(err));
    // });
  } catch (err) {
    console.error("❌ Ошибка при обрезке изображения:", err);
    console.error(err.stack)
    throw err;
  }
}

// Функция предобработки изображения (только grayscale)
async function preprocessImage(buffer) {
  try {
    if (!buffer || !Buffer.isBuffer(buffer)) {
      throw new Error("Буфер изображения некорректный!");
    }

    const type = await fileType.fileTypeFromBuffer(buffer);
    if (!type || !type.mime.startsWith('image/')) {
      throw new Error(`❌ Неподдерживаемый MIME тип: ${type?.mime || 'неизвестно'}`);
    }

    // Сначала обрезаем изображение до 200x200
    const croppedBuffer = await cropTo200x200(buffer);
    const image = await Jimp.read(croppedBuffer);

    return new Promise((resolve, reject) => {
      image
        .greyscale() // Оставляем только перевод в градации серого
        .contrast(1)
        .brightness(1.2)
        
        .getBuffer('image/jpeg')
        .then(processedBuffer => resolve(processedBuffer))
        .catch(err => reject(err));
    });
  } catch (err) {
    console.error("❌ Ошибка при обработке изображения:", err);
    throw err;
  }
}

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

            const filename = `${downloadDir}/photo_${Date.now()}.${type.ext}`;
            fs.writeFileSync(filename, buffer);

            console.log("📄 Начинаем обрезку изображения до 200x200...");
            const croppedBuffer = await cropTo200x200(buffer);
            const croppedFilename = `${downloadDir}/cropped_${Date.now()}.jpg`;
            fs.writeFileSync(croppedFilename, croppedBuffer);

            console.log("📄 Начинаем предобработку изображения...");
            const processedBuffer = await preprocessImage(buffer);

            const processedFilename = `${downloadDir}/processed_${Date.now()}.png`;
            fs.writeFileSync(processedFilename, processedBuffer);

            console.log("📄 Начинаем распознавание текста...");
            const { data: { text } } = await Tesseract.recognize(
              processedBuffer,
              'eng+rus',
              {
                config: {
                  psm: 6,
                }
              }
            );

            const cleanedText = text
              .replace(/[^\w\s.,?!—\u0400-\u04FF]/g, '')
              .replace(/\s+/g, ' ')
              .trim();

            console.log("📄 Распознанный текст:\n", cleanedText);

            await client.sendMessage("me", {
              message: `🔔 Новый пост в "${channel.title}"\n\n📄 Распознанный текст:\n${cleanedText || "Не найдено"}\n\n✂️ Обработана область 200x200 пикселей`,
              file: croppedFilename
            });

          } catch (err) {
            console.error("❌ Ошибка при обработке изображения:", err);
          }
        }

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
    console.error("Ошибка:", err);
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
