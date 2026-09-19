/* Пульт админа: список людей и переписка с ними.
   Ванильный JS без сборки — пульт маленький, а лишний шаг сборки означал бы,
   что правку в нём нельзя выложить так же просто, как правку бота. */
(function () {
  'use strict';

  var tg = window.Telegram && window.Telegram.WebApp;
  var initData = (tg && tg.initData) || '';

  var REFRESH_MS = 15000;      // как часто подтягивать новое
  var TYPING_PAUSE = 350;      // пауза после ввода перед поиском

  var state = { id: null, onlyChats: true, query: '', busy: false, person: null,
                editing: null, card: null, chats: null };

  var $ = function (id) { return document.getElementById(id); };
  var listScreen = $('list');
  var chatScreen = $('chat');

  // --------------------------------------------------------------- сеть

  function api(path, body) {
    return fetch('/api/' + path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(Object.assign({ initData: initData }, body || {}))
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (!res.ok) { throw new Error(data.error || ('ошибка ' + res.status)); }
        return data;
      });
    });
  }

  // Пульт открыли мимо Telegram (обычной ссылкой) — подписи нет, и ни один
  // запрос не пройдёт. Показываем это прямо, а не тостом «нет подписи».
  function fatal(title, hint) {
    var box = document.createElement('div');
    box.className = 'fatal';
    var head = document.createElement('b');
    head.textContent = title;
    var text = document.createElement('p');
    text.textContent = hint;
    box.appendChild(head);
    box.appendChild(text);
    document.body.textContent = '';
    document.body.appendChild(box);
  }

  function toast(message) {
    var box = $('toast');
    box.textContent = message;
    box.hidden = false;
    clearTimeout(box._timer);
    box._timer = setTimeout(function () { box.hidden = true; }, 3200);
  }

  // ------------------------------------------------------------- показ

  function when(ts) {
    if (!ts) { return ''; }
    var date = new Date(ts * 1000);
    var today = new Date();
    var sameDay = date.toDateString() === today.toDateString();
    return sameDay
      ? date.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
      : date.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' });
  }

  // Откуда человек пришёл — по-русски: в базе это технические метки ссылок.
  var SOURCES = {
    zayavka: 'с заявки на сайте', sayt: 'с сайта', site: 'с сайта',
    instagram: 'из Instagram', tg: 'из Telegram', vk: 'из ВК'
  };

  var KINDS = {
    photo: '📷 фото', video: '🎬 видео', voice: '🎤 голосовое',
    video_note: '⭕ кружок', document: '📎 файл', audio: '🎵 аудио',
    sticker: '🙂 стикер', animation: '🎞 гифка'
  };

  function preview(person) {
    if (!person.last_at) {
      return person.launched ? 'в марафоне, ещё не писал(а)' : 'зашёл(-шла) в бота';
    }
    var body = person.last_kind && person.last_kind !== 'text'
      ? (KINDS[person.last_kind] || 'вложение')
      : person.last_text;
    return (person.last_side === 'out' ? 'Вы: ' : '') + (body || '');
  }

  function initials(name) {
    var clean = (name || '?').trim();
    return clean ? clean[0].toUpperCase() : '?';
  }

  function personRow(person) {
    var row = document.createElement('button');
    row.type = 'button';
    row.className = 'person';
    row.dataset.id = person.id;

    var ava = document.createElement('span');
    ava.className = 'ava' + (person.banned ? ' ava--ban' : '');
    ava.textContent = person.banned ? '🚫' : initials(person.name);

    var main = document.createElement('span');
    var name = document.createElement('span');
    name.className = 'person__name';
    name.textContent = person.name + (person.username ? ' · @' + person.username : '');
    var last = document.createElement('span');
    last.className = 'person__last';
    last.textContent = preview(person);
    main.appendChild(name);
    main.appendChild(document.createElement('br'));
    main.appendChild(last);

    var side = document.createElement('span');
    side.className = 'person__side';
    var at = document.createElement('span');
    at.className = 'person__at';
    at.textContent = when(person.last_at);
    side.appendChild(at);
    if (person.waiting) {
      var badge = document.createElement('span');
      badge.className = 'badge';
      badge.textContent = person.waiting;
      side.appendChild(badge);
    } else if (person.blocked) {
      var gone = document.createElement('span');
      gone.className = 'badge badge--quiet';
      gone.textContent = 'ушёл';
      side.appendChild(gone);
    }

    row.appendChild(ava);
    row.appendChild(main);
    row.appendChild(side);
    row.addEventListener('click', function () { openChat(person.id); });
    return row;
  }

  function drawPeople(people, everyone) {
    var box = $('people');
    box.textContent = '';
    // Переписка копится с 18.09.2026: пока её нет, показываем всех и
    // говорим об этом, чтобы пустой список не выглядел поломкой.
    if (everyone) {
      var note = document.createElement('p');
      note.className = 'hint';
      note.textContent = 'Переписок пока нет — они копятся с того дня, как включили пульт. '
        + 'Ниже все, кто заходил в бота: откройте любого и напишите первым.';
      box.appendChild(note);
    }
    people.forEach(function (person) { box.appendChild(personRow(person)); });
    $('empty').hidden = people.length > 0;
    var waiting = people.filter(function (p) { return p.waiting; }).length;
    $('count').textContent = waiting ? waiting + ' ждут ответа' : people.length + ' чел.';
  }

  function loadPeople() {
    return api('people', { query: state.query, onlyChats: state.onlyChats })
      .then(function (data) { drawPeople(data.people || [], data.everyone); })
      .catch(function (err) {
        if (!initData) { return noEntry(); }
        toast(err.message);
      });
  }

  function noEntry() {
    fatal('Пульт открывается из бота',
          'Откройте Telegram, напишите боту команду /пульт и нажмите кнопку — '
          + 'по обычной ссылке пульт не пустит: Telegram подписывает вход, '
          + 'и только по этой подписи бот понимает, что это свои.');
  }

  // ------------------------------------------------------------ диалог

  function messageRow(message) {
    var row = document.createElement('div');
    // Шаг воронки — не сообщение, а пометка: что бот прислал сам и что
    // человек нажал. Без неё «Да» висело в диалоге без вопроса.
    if (message.note) {
      row.className = 'note';
      row.textContent = message.text + ' · ' + when(message.at);
      return row;
    }
    row.className = 'msg' + (message.mine ? ' msg--mine' : '');
    var body = message.kind && message.kind !== 'text'
      ? (KINDS[message.kind] || 'вложение') + (message.text ? '\n' + message.text : '')
      : (message.text || '');
    if (message.kind && message.kind !== 'text') { row.classList.add('msg--media'); }
    row.textContent = body;
    var at = document.createElement('span');
    at.className = 'msg__at';
    at.textContent = when(message.at);
    row.appendChild(at);

    // Своё сообщение можно поправить или убрать у человека: нажатие
    // открывает под ним две кнопки (AleX 19.09.2026).
    if (message.mine && (message.can_edit || message.can_drop)) {
      row.classList.add('msg--own');
      row.addEventListener('click', function (event) {
        if (event.target.tagName === 'BUTTON') { return; }
        showActions(row, message);
      });
    }
    return row;
  }

  function actionButton(title, onClick) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'msg__do';
    button.textContent = title;
    button.addEventListener('click', onClick);
    return button;
  }

  function showActions(row, message) {
    var old = row.querySelector('.msg__acts');
    if (old) { old.remove(); return; }             // второе нажатие — закрыть
    var box = document.createElement('div');
    box.className = 'msg__acts';
    if (message.can_edit) {
      box.appendChild(actionButton('Изменить', function () { startEdit(message); }));
    }
    if (message.can_drop) {
      box.appendChild(actionButton('Удалить', function () { dropMessage(message); }));
    }
    row.appendChild(box);
  }

  function startEdit(message) {
    state.editing = message.id;
    var field = $('text');
    field.value = message.text || '';
    grow(field);
    field.focus();
    $('go').textContent = '✓';
    toast('Поправьте текст и нажмите ✓ — сообщение изменится и у человека');
  }

  function stopEdit() {
    state.editing = null;
    $('go').textContent = '➤';
  }

  function dropMessage(message) {
    var ask = 'Удалить это сообщение? Оно исчезнет и у человека.';
    var run = function () {
      api('drop', { messageId: message.id })
        .then(function (data) {
          drawChat({ person: state.person, card: state.card, chats: state.chats,
                     messages: data.messages || [] });
        })
        .catch(function (err) { toast(err.message); });
    };
    if (tg && tg.showConfirm) { tg.showConfirm(ask, function (ok) { if (ok) { run(); } }); }
    else if (window.confirm(ask)) { run(); }
  }

  // Карточка участника: когда пришёл, что получил и что ответил по каждому
  // дню, откуда пришёл и в каких чатах Павла состоит (AleX 18.09.2026).
  var ANSWERS = { yes: 'ответил «Да»', no: 'ответил «Нет»' };

  function moment(ts) {
    if (!ts) { return ''; }
    var d = new Date(ts * 1000);
    return d.toLocaleDateString('ru-RU', { day: '2-digit', month: '2-digit' })
      + ' ' + d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  }

  function cardLine(title, value) {
    var row = document.createElement('div');
    row.className = 'card__row';
    var left = document.createElement('span');
    left.className = 'card__key';
    left.textContent = title;
    var right = document.createElement('span');
    right.textContent = value;
    row.appendChild(left);
    row.appendChild(right);
    return row;
  }

  function drawCard(data) {
    var box = $('card');
    box.textContent = '';
    var card = data.card || state.card || {};
    var person = data.person || {};

    box.appendChild(cardLine('Зашёл в бота', moment(card.started_at) || 'неизвестно'));
    box.appendChild(cardLine('Запустил марафон', card.launched_at
      ? moment(card.launched_at) + (card.launches > 1 ? ' · запусков: ' + card.launches : '')
      : 'не запускал'));
    box.appendChild(cardLine('Откуда пришёл',
      (SOURCES[person.source] || person.source || 'напрямую')
      + (card.lead_no ? ' · заявка №' + card.lead_no : '')));

    (card.days || []).forEach(function (item) {
      var value = item.at ? 'получил ' + moment(item.at) : 'ещё не получил';
      if (item.answer) { value += ' · ' + (ANSWERS[item.answer] || item.answer); }
      else if (item.at && item.day < 4) { value += ' · без ответа'; }
      box.appendChild(cardLine('День ' + item.day, value));
    });

    var chats = data.chats || state.chats || [];
    box.appendChild(cardLine('Чаты ФИНИШ', chats.length
      ? chats.map(function (c) { return c.title + ' — ' + c.status; }).join('; ')
      : 'бот не состоит ни в одном чате — добавьте его, и будет видно'));
  }

  function drawChat(data) {
    state.person = data.person;
    state.card = data.card || state.card;
    state.chats = data.chats || state.chats;
    var person = data.person;
    $('chat-name').textContent = person.name;
    var bits = [];
    if (person.username) { bits.push('@' + person.username); }
    bits.push('ID ' + person.id);
    if (person.blocked) { bits.push('закрыл бота'); }
    else if (person.poll) { bits.push('ждём ответ: ' + person.poll.replace('day', 'день ')); }
    if (person.source) { bits.push(SOURCES[person.source] || person.source); }
    $('chat-sub').textContent = bits.join(' · ');
    $('ban').classList.toggle('is-on', !!person.banned);
    $('ban').textContent = person.banned ? '↩️' : '🚫';

    drawCard(data);

    var log = $('log');
    log.textContent = '';
    if (!data.messages.length) {
      var empty = document.createElement('p');
      empty.className = 'log__empty';
      empty.textContent = 'Переписки ещё не было.\nНапишите первым — сообщение уйдёт от имени бота.';
      log.appendChild(empty);
    } else {
      data.messages.forEach(function (m) { log.appendChild(messageRow(m)); });
    }
    log.scrollTop = log.scrollHeight;
    $('go').disabled = !!person.blocked;
  }

  function openChat(id) {
    state.id = id;
    listScreen.classList.add('is-behind');
    chatScreen.classList.add('is-open');
    $('log').textContent = '';
    $('card').hidden = true;
    $('info').classList.remove('is-on');
    stopEdit();
    if (tg && tg.BackButton) { tg.BackButton.show(); }
    api('chat', { id: id }).then(drawChat).catch(function (err) { toast(err.message); });
  }

  function closeChat() {
    state.id = null;
    chatScreen.classList.remove('is-open');
    listScreen.classList.remove('is-behind');
    if (tg && tg.BackButton) { tg.BackButton.hide(); }
    loadPeople();
  }

  function send(event) {
    event.preventDefault();
    var field = $('text');
    var link = $('media');
    var text = field.value.trim();
    var media = link.value.trim();
    if ((!text && !media) || state.busy || !state.id) { return; }
    state.busy = true;
    $('go').disabled = true;
    var call = state.editing
      ? api('edit', { messageId: state.editing, text: text })
      : api('send', { id: state.id, text: text, media: media });
    call
      .then(function (data) {
        field.value = '';
        link.value = '';
        link.hidden = true;
        $('clip').classList.remove('is-on');
        stopEdit();
        grow(field);
        drawChat({ person: state.person, messages: data.messages || [] });
        if (tg && tg.HapticFeedback) { tg.HapticFeedback.notificationOccurred('success'); }
      })
      .catch(function (err) { toast(err.message); })
      .then(function () { state.busy = false; $('go').disabled = false; });
  }

  function toggleBan() {
    if (!state.id || !state.person) { return; }
    var undo = !!state.person.banned;
    var ask = undo
      ? 'Убрать из чёрного списка и снять баны в каналах?'
      : 'В чёрный список? Забаним и в каналах, где бот админ.';
    var run = function () {
      api('ban', { id: state.id, undo: undo })
        .then(function (data) {
          state.person.banned = data.banned;
          $('ban').classList.toggle('is-on', !!data.banned);
          $('ban').textContent = data.banned ? '↩️' : '🚫';
          toast(data.banned ? 'В чёрном списке' : 'Убрали из чёрного списка');
        })
        .catch(function (err) { toast(err.message); });
    };
    if (tg && tg.showConfirm) { tg.showConfirm(ask, function (ok) { if (ok) { run(); } }); }
    else if (window.confirm(ask)) { run(); }
  }

  // -------------------------------------------------------------- связи

  $('back').addEventListener('click', closeChat);
  // Вложение — ссылкой: файл уходит от Telegram напрямую, минуя пульт.
  $('clip').addEventListener('click', function () {
    var link = $('media');
    link.hidden = !link.hidden;
    this.classList.toggle('is-on', !link.hidden);
    if (!link.hidden) { link.focus(); }
  });

  $('info').addEventListener('click', function () {
    var box = $('card');
    box.hidden = !box.hidden;
    this.classList.toggle('is-on', !box.hidden);
  });

  $('ban').addEventListener('click', toggleBan);
  $('send').addEventListener('submit', send);

  $('text').addEventListener('keydown', function (event) {
    if (event.key === 'Enter' && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      send(event);
    }
  });

  // Высота поля: не меньше трёх строк и не больше шести — дальше прокрутка.
  var MIN_ROWS_PX = 82;
  var MAX_ROWS_PX = 160;

  function grow(field) {
    field.style.height = 'auto';
    field.style.height = Math.min(Math.max(field.scrollHeight, MIN_ROWS_PX), MAX_ROWS_PX) + 'px';
  }

  $('text').addEventListener('input', function () { grow(this); });

  var typing;
  $('search').addEventListener('input', function () {
    var value = this.value;
    clearTimeout(typing);
    typing = setTimeout(function () {
      state.query = value;
      loadPeople();
    }, TYPING_PAUSE);
  });

  Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (tab) {
    tab.addEventListener('click', function () {
      Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (other) {
        other.classList.toggle('is-on', other === tab);
      });
      state.onlyChats = tab.dataset.only === '1';
      loadPeople();
    });
  });

  if (tg) {
    tg.ready();
    tg.expand();
    if (tg.BackButton) { tg.BackButton.onClick(closeChat); }
    if (tg.setHeaderColor) { try { tg.setHeaderColor('secondary_bg_color'); } catch (e) { /* старый клиент */ } }
  }

  // Пульт открыли обычной ссылкой, мимо бота: подписи нет, дёргать бота
  // незачем — объясняем это и на этом заканчиваем.
  if (!initData) {
    noEntry();
    return;
  }

  // Новое подтягиваем сами: человек мог ответить, пока пульт открыт.
  setInterval(function () {
    if (document.hidden) { return; }
    if (state.id) {
      api('chat', { id: state.id }).then(drawChat).catch(function () { /* сеть моргнула */ });
    } else {
      loadPeople();
    }
  }, REFRESH_MS);

  // Кнопка «Ответить в пульте» под уведомлением открывает сразу диалог:
  // адрес пульта приходит с ?id= того человека, о котором уведомление.
  loadPeople();
  var wanted = parseInt(new URLSearchParams(location.search).get('id'), 10);
  if (wanted) { openChat(wanted); }
}());
