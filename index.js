const { Telegraf } = require('telegraf');

const bot = new Telegraf('7165098170:AAHpAsK0FKtePatuiCRUCzBlpMaFUft7Ods');

bot.start((ctx) => ctx.reply('Привет! Я телеграмм-бот на JavaScript.'));
bot.help((ctx) => ctx.reply('Отправь мне любое сообщение, и я отвечу тем же!'));
bot.on('message', (ctx) => {
  ctx.reply(`Вы написали: ${ctx.message.text}`);
});

bot.launch();

console.log('Бот запущен...');
require('dotenv').config();

const apiId = parseInt(process.env.API_ID);
const apiHash = process.env.API_HASH;
console.log(apiHash,apiId)