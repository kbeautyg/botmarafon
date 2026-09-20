/* Пульт админа: список людей и переписка с ними.
   Ванильный JS без сборки — пульт маленький, а лишний шаг сборки означал бы,
   что правку в нём нельзя выложить так же просто, как правку бота. */
(function () {
  'use strict';

  var tg = window.Telegram && window.Telegram.WebApp;
  var initData = (tg && tg.initData) || '';

  var REFRESH_MS = 15000;      // как часто подтягивать новое
  var TYPING_PAUSE = 350;      // пауза после ввода перед поиском

  /* picking — в списке включены галочки; picked — кого отметили; to —
     кому уйдёт следующее сообщение из окна переписки (пусто — одному,
     тому, чей чат открыт). AleX 20.09.2026: «выбранным из списка». */
  var state = { id: null, onlyChats: true, query: '', busy: false, person: null,
                editing: null, card: null, chats: null,
                picking: false, picked: [], to: [] };

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

    /* В режиме выбора строка не открывает переписку, а ставит галочку:
       промахнуться пальцем и вместо отметки уйти в чужой диалог, потеряв
       весь набранный список, слишком легко. */
    if (state.picking && !person.banned) {
      var mark = document.createElement('span');
      mark.className = 'tick';
      row.insertBefore(mark, ava);
      row.classList.toggle('is-picked', state.picked.indexOf(person.id) > -1);
      row.addEventListener('click', function () { togglePick(person.id, row); });
    } else if (state.picking) {
      // Из чёрного списка: бот с ним не работает — отмечать нечего.
      var off = document.createElement('span');
      off.className = 'tick tick--off';
      row.insertBefore(off, ava);
      row.classList.add('is-off');
    } else {
      row.addEventListener('click', function () { openChat(person.id); });
    }
    return row;
  }

  // ---------------------------------------------------------- выбор людей

  function togglePick(id, row) {
    var at = state.picked.indexOf(id);
    if (at > -1) { state.picked.splice(at, 1); } else { state.picked.push(id); }
    if (row) { row.classList.toggle('is-picked', at < 0); }
    drawPicked();
  }

  function drawPicked() {
    var bar = $('picked');
    bar.hidden = !state.picking;
    $('picked-count').textContent = 'Выбрано: ' + state.picked.length;
    $('picked-write').disabled = state.picked.length === 0;
  }

  function setPicking(on) {
    state.picking = on;
    if (!on) { state.picked = []; }
    $('pick').classList.toggle('is-on', on);
    $('pick').textContent = on ? 'Отмена' : 'Выбрать';
    drawPicked();
    loadPeople();
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

  /* Окно переписки, но получателей много. Карточки и чёрного списка тут
     нет — они про одного человека, — а поле ввода, вложения, голосовое и
     кружок работают как обычно: одно и то же уйдёт каждому. */
  function openMany() {
    if (!state.picked.length) { return; }
    state.to = state.picked.slice();
    state.id = null;
    state.person = null;
    listScreen.classList.add('is-behind');
    chatScreen.classList.add('is-open');
    stopEdit();
    $('card').hidden = true;
    $('info').hidden = true;
    $('ban').hidden = true;
    $('chat-name').textContent = 'Выбрано: ' + state.to.length;
    $('chat-sub').textContent = 'напишем каждому одно и то же';
    $('log').textContent = '';
    var hint = document.createElement('p');
    hint.className = 'hint';
    hint.textContent = 'Сообщение придёт каждому из выбранных отдельно, '
      + 'как обычное сообщение от бота — с уведомлением. '
      + 'Переписку видно в карточке каждого.';
    $('log').appendChild(hint);
    $('go').disabled = false;
    if (tg && tg.BackButton) { tg.BackButton.show(); }
  }

  function openChat(id) {
    state.to = [];
    $('info').hidden = false;
    $('ban').hidden = false;
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
    state.to = [];
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
    var many = state.to.length;
    if ((!text && !media) || state.busy || (!state.id && !many)) { return; }
    state.busy = true;
    $('go').disabled = true;
    var call = state.editing
      ? api('edit', { messageId: state.editing, text: text })
      : api('send', { id: state.id, ids: state.to, text: text, media: media });
    call
      .then(function (data) {
        field.value = '';
        link.value = '';
        link.hidden = true;
        $('clip').classList.remove('is-on');
        stopEdit();
        grow(field);
        if (many) { doneMany(data); } else {
          drawChat({ person: state.person, messages: data.messages || [] });
        }
        if (tg && tg.HapticFeedback) { tg.HapticFeedback.notificationOccurred('success'); }
      })
      .catch(function (err) { toast(err.message); })
      .then(function () { state.busy = false; $('go').disabled = false; });
  }

  /* Итог отправки выбранным. Закрывшие бота и сбои названы отдельно:
     «ушло 18 из 20» без объяснения выглядит как поломка. */
  function doneMany(data) {
    var parts = ['Отправлено: ' + (data.sent || 0)];
    if (data.gone) { parts.push('закрыли бота: ' + data.gone); }
    if (data.failed) { parts.push('не дошло: ' + data.failed); }
    toast(parts.join(' · '));
    closeChat();
    setPicking(false);
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

  // ------------------------------------------------- голосовое и кружок
  //
  // Записываем прямо в пульте (AleX 19.09.2026). Браузер отдаёт webm, а
  // Telegram принимает голосовое только ogg/opus и кружок только
  // квадратным mp4 — перекодирует бот, здесь наше дело записать.

  var REC_LIMIT_MS = 60000;              // минута: столько держит кружок
  var recorder = null;
  var recChunks = [];
  var recKind = 'voice';
  var recStarted = 0;
  var recTimer = null;
  var recStream = null;

  function recTick() {
    var passed = Math.floor((Date.now() - recStarted) / 1000);
    $('rec-time').textContent = Math.floor(passed / 60) + ':' + ('0' + (passed % 60)).slice(-2);
    if (passed * 1000 >= REC_LIMIT_MS) { finishRecord(true); }
  }

  function stopStream() {
    if (recStream) {
      recStream.getTracks().forEach(function (track) { track.stop(); });
      recStream = null;
    }
    clearInterval(recTimer);
    $('rec').hidden = true;
    $('preview').hidden = true;
    $('preview').srcObject = null;
  }

  function startRecord(kind) {
    if (recorder) { return; }
    if (!navigator.mediaDevices || !window.MediaRecorder) {
      toast('Этот телефон не даёт записывать прямо в пульте. Ответьте голосовым '
            + 'реплаем в самом боте — дойдёт так же.');
      return;
    }
    recKind = kind;
    var wants = kind === 'note'
      ? { audio: true, video: { facingMode: 'user', width: 480, height: 480 } }
      : { audio: true };
    navigator.mediaDevices.getUserMedia(wants).then(function (stream) {
      recStream = stream;
      recChunks = [];
      recorder = new MediaRecorder(stream);
      recorder.ondataavailable = function (event) {
        if (event.data && event.data.size) { recChunks.push(event.data); }
      };
      recorder.start();
      recStarted = Date.now();
      $('rec').hidden = false;
      $('rec-time').textContent = '0:00';
      recTimer = setInterval(recTick, 250);
      if (kind === 'note') {
        var video = $('preview');
        video.hidden = false;
        video.srcObject = stream;
        video.play().catch(function () { /* показ превью — не главное */ });
      }
    }).catch(function () {
      toast(kind === 'note'
        ? 'Телефон не дал доступ к камере. Разрешите его для Telegram или '
          + 'отправьте кружок реплаем в самом боте.'
        : 'Телефон не дал доступ к микрофону. Разрешите его для Telegram или '
          + 'отправьте голосовое реплаем в самом боте.');
    });
  }

  function finishRecord(send) {
    if (!recorder) { return; }
    var kind = recKind;
    recorder.onstop = function () {
      var blob = new Blob(recChunks, { type: recChunks[0] ? recChunks[0].type : 'video/webm' });
      recorder = null;
      stopStream();
      if (send && blob.size) { uploadRecord(blob, kind); }
    };
    try { recorder.stop(); } catch (e) { recorder = null; stopStream(); }
  }

  function uploadRecord(blob, kind) {
    var many = state.to.length;
    var form = new FormData();
    form.append('initData', initData);
    form.append('id', String(state.id || 0));
    if (many) { form.append('ids', state.to.join(',')); }
    form.append('kind', kind);
    form.append('text', $('text').value.trim());
    form.append('file', blob, kind === 'note' ? 'note.webm' : 'voice.webm');
    state.busy = true;
    $('go').disabled = true;
    toast(kind === 'note' ? 'Отправляю кружок…' : 'Отправляю голосовое…');
    fetch('/api/record', { method: 'POST', body: form })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          if (!res.ok) { throw new Error(data.error || ('ошибка ' + res.status)); }
          return data;
        });
      })
      .then(function (data) {
        $('text').value = '';
        grow($('text'));
        if (many) { doneMany(data); return; }
        drawChat({ person: state.person, card: state.card, chats: state.chats,
                   messages: data.messages || [] });
        if (data.kind === 'document') {
          toast('Отправили файлом: перекодировать запись не вышло.');
        }
      })
      .catch(function (err) { toast(err.message); })
      .then(function () { state.busy = false; $('go').disabled = false; });
  }

  $('mic').addEventListener('click', function () { startRecord('voice'); });
  $('cam').addEventListener('click', function () { startRecord('note'); });
  $('rec-stop').addEventListener('click', function () { finishRecord(true); });
  $('rec-drop').addEventListener('click', function () { finishRecord(false); });

  // -------------------------------------------------------------- связи

  $('back').addEventListener('click', closeChat);
  $('pick').addEventListener('click', function () { setPicking(!state.picking); });
  drawPicked();                      // полоса выбора закрыта, «Написать» погашено
  $('picked-clear').addEventListener('click', function () { setPicking(false); });
  $('picked-write').addEventListener('click', openMany);
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
