require('dotenv').config();
const { TelegramClient } = require("telegram");
const { NewMessage } = require("telegram/events");
const { StringSession } = require("telegram/sessions");
const Tesseract = require('tesseract.js');
const axios = require('axios')
const input = require("input");
const fs = require("fs");
const { Jimp, JimpMime } = require("jimp");
const fileType = require('file-type');

const apiId = parseInt(process.env.API_ID);
const apiHash = process.env.API_HASH;
const stringSession = new StringSession(process.env.SESSION);

// const OCR_URL = process.env.OCR_URL || 'http://localhost:8000/ocr';

async function sendToOCR(buffer, { focus = 'full' } = {}) {
  const form = new FormData();
   const blob = new Blob([buffer], { type: 'image/jpeg' });
  form.append('image', blob, { filename: `image_${Date.now()}.jpg`, contentType: 'image/jpeg' });
  form.append('focus', focus);

  const res = await axios.post(process.env.OCR_URL, form);
  return res.data;
}
// Основной клиент
(async () => {
  const client = new TelegramClient(stringSession, apiId, apiHash, { connectionRetries: 5 });
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
        console.log("Новый пост:", msg.message || '<media or unnamed>');

        if (msg.photo) {
          try {
            const buffer = await client.downloadMedia(msg);
            const type = await fileType.fileTypeFromBuffer(buffer);
            if (!type || !type.mime.startsWith('image/')) return;

            // Отправляем в Python: сначала обычный OCR
            const ocrFull = await sendToOCR(buffer, { focus: 'full' });
            // При необходимости — распознать только обведённую область
             const ocrHighlight = await sendToOCR(buffer, { focus: 'highlight' });
             console.log(ocrFull)
            console.log("OCR full_text:", ocrFull.ocr.full_text);
            // console.log("OCR highlighted_text:", ocrHighlight.highlighted_text);

            // Пример: переслать себе результат
            await client.sendMessage("me", {
              message:
                `Канал: ${channel.title}\n` +
                `full_text: ${ocrFull.full_text || '-'}\n` +
                `highlighted: ${ocrHighlight.highlighted_text || '-'}\n` +
                `mask_present: ${ocrHighlight.mask_present}`
            });

          } catch (err) {
            console.error("Ошибка при обработке изображения:", err);
          }
        } else {
          // Текстовые посты (при желании можно тоже анализировать)
          await client.sendMessage("me", { message: `Новый пост в "${channel.title}":\n\n${msg.message}` });
        }
      },
      new NewMessage({ chats: [channel.id] })
    );
  } catch (err) {
    console.error("Ошибка работы с каналом:", err);
  }
})();

