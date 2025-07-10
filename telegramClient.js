
// const { TelegramClient } = require("telegram");
// const { StringSession } = require("telegram/sessions");
// const input = require("input"); // npm install input

// const apiId = parseInt(process.env.API_ID) // ← замени на свой
// const apiHash = process.env.API_HASH; // ← замени на свой
// const stringSession = new StringSession(process.env.SESSION); // пустая сессия

// (async () => {
//   const client = new TelegramClient(stringSession, apiId, apiHash, {
//     connectionRetries: 5,
//   });

//   console.log("Запуск клиента...");

//   await client.start({
//     phoneNumber: async () => await input.text("Введите номер телефона: "),
//     phoneCode: async () => await input.text("Введите код из Telegram: "),
//     phoneCode: async () =>
//       await input.text("Please enter the code you received: "),
//     onError: (err) => console.log(err),
//   });

//   console.log("Вы успешно подключены!");
//   console.log("Сохраните эту строку для повторного входа без кода:", client.session.save());

//   await client.sendMessage("me", { message: "Привет от Telegram-клиента!" });


//   try {
//     // Получаем сущность канала по username
//     const channel = await client.getEntity("primerABCD");

//     console.log(`Подписываемся на обновления канала: ${channel.title}`);

//     // Подписываемся на новые сообщения в этом канале
//     client.addEventHandler(async (event) => {
//       if (event.isNewMessage && event.chatId === channel.id) {
//         const message = event.message;

//         console.log("Новый пост в канале:", message.message);

//         // Пересылаем себе текст сообщения
//         await client.sendMessage("me", {
//           message: `🔔 Новый пост в "${channel.title}":\n\n${message.message}`
//         });
//       }
//     }, {});

//   } catch (error) {
//     console.error("Ошибка при получении информации о канале:", error);
//   }
// })();


require('dotenv').config();
const fs = require('fs');
const path = require('path');
const { TelegramClient } = require("telegram");
const { StringSession } = require("telegram/sessions");
const { NewMessage } = require("telegram/events");
const input = require("input"); // npm install input

// Папка для сохранения изображений
const downloadDir = path.resolve(__dirname, 'downloads');
if (!fs.existsSync(downloadDir)) {
  fs.mkdirSync(downloadDir, { recursive: true });
}

// Загружаем данные из .env
const apiId = parseInt(process.env.API_ID); // ← должен быть числом
const apiHash = process.env.API_HASH; // ← строка
const stringSession = new StringSession(process.env.SESSION || ""); // загружаем сессию или начинаем новую

(async () => {
  const client = new TelegramClient(stringSession, apiId, apiHash, {
    connectionRetries: 5,
  });

  console.log("Запуск клиента...");

  await client.start({
    phoneNumber: async () => await input.text("Введите номер телефона: "),
    phoneCode: async () => await input.text("Введите код из Telegram: "),
    onError: (err) => console.error("Ошибка при подключении:", err),
  });

  console.log("Вы успешно подключены!");
  console.log("Сохраните эту строку для повторного входа без кода:", client.session.save());
  if (!process.env.SESSION) {
    console.log("Обновите SESSION в .env следующим значением:");
    console.log(client.session.save());
  }

  // Отправляем приветственное сообщение себе
  await client.sendMessage("me", { message: "Привет от Telegram-клиента!" });

  try {
    // Получаем сущность канала по username
    const channel = await client.getEntity("primerABCD");
    console.log(`Подписываемся на обновления канала: ${channel.title}`);

    // Регистрируем хендлер для новых сообщений и постов в канале
    client.addEventHandler(
      async (event) => {
        const msg = event.message;
        console.log("Новый пост или сообщение в канале:", msg.message || '<media or unnamed>');

        // Логируем и скачиваем изображение
        if (msg.photo) {
          console.log("🚀 Этот пост содержит изображение. Скачиваем...");
          try {
            // GramJS: передаём объект Message и папку назначения
            const filePath = await client.downloadMedia(msg, downloadDir);
            console.log(`✅ Изображение сохранено по пути: ${filePath}`);
          } catch (err) {
            console.error("Ошибка при скачивании изображения:", err);
          }
        }

        // Если в сообщении есть медиа, пересылаем его автоматически
        if (msg.media) {
          await client.forwardMessages(
            "me",                // куда переслать
            { messages: [msg], fromPeer: channel }
          );
        } else {
          // Иначе пересылаем текст
          await client.sendMessage("me", {
            message: `🔔 Новый пост в "${channel.title}":\n\n${msg.message}`
          });
        }
      },
      new NewMessage({ chats: [channel.id] })
    );

  } catch (error) {
    console.error("Ошибка при получении информации о канале:", error);
  }

})();