/* Пульт админа: список людей и переписка с ними.
   Ванильный JS без сборки — пульт маленький, а лишний шаг сборки означал бы,
   что правку в нём нельзя выложить так же просто, как правку бота. */
(function () {
  'use strict';

  var tg = window.Telegram && window.Telegram.WebApp;
  var initData = (tg && tg.initData) || '';

  var REFRESH_MS = 15000;      // как часто подтягивать новое
  var TYPING_PAUSE = 350;      // пауза после ввода перед поиском
  var PAGE = 60;               // столько людей приходит одной порцией

  /* picking — в списке включены галочки; picked — кого отметили; to —
     кому уйдёт следующее сообщение из окна переписки (пусто — одному,
     тому, чей чат открыт). AleX 20.09.2026: «выбранным из списка».

     scope — выбрана целая группа: все участники или отдельный шаг
     воронки. Тогда получателей считает бот, а не пульт: их сотни, и
     список id из телефона устарел бы раньше, чем по нему нажали
     (AleX 23.09.2026). skip — кому из группы не писать: галочки стоят у
     всех, и снять их можно у тех, кому сообщение уже ушло.
     file — файл с устройства, ждёт отправки. */
  var state = { id: null, onlyChats: true, query: '', busy: false, person: null,
                editing: null, card: null, chats: null,
                picking: false, picked: [], to: [],
                offset: 0, total: 0, more: 0, loaded: 0, everyone: false,
                scopes: null, scope: '', scopeTitle: '', scopeCount: 0, skip: [],
                rows: [], file: null };

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

  /* sticky — сообщение висит, пока его не сменят: так показываем ход
     выгрузки файла, которая на телефоне может идти минуту. */
  function toast(message, sticky) {
    var box = $('toast');
    box.textContent = message;
    box.hidden = false;
    clearTimeout(box._timer);
    if (!sticky) { box._timer = setTimeout(function () { box.hidden = true; }, 3200); }
  }

  /* Отправка формы с файлом. Не fetch: у XHR виден ход выгрузки, а без
     него сорок мегабайт с телефона уходят в тишину. */
  function post(path, form, onProgress) {
    return new Promise(function (resolve, reject) {
      var xhr = new XMLHttpRequest();
      xhr.open('POST', '/api/' + path);
      if (onProgress && xhr.upload) {
        xhr.upload.onprogress = function (event) {
          if (event.lengthComputable) {
            onProgress(Math.round(event.loaded * 100 / event.total));
          }
        };
      }
      xhr.onload = function () {
        var data = {};
        try { data = JSON.parse(xhr.responseText); } catch (e) { data = {}; }
        if (xhr.status >= 200 && xhr.status < 300) { resolve(data); }
        else { reject(new Error(data.error || ('ошибка ' + xhr.status))); }
      };
      xhr.onerror = function () { reject(new Error('связь оборвалась')); };
      xhr.send(form);
    });
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
    if (state.picking && state.scope) {
      /* В группе отмечены все: список показывает ровно тех, кто получит
         сообщение. Нажатие снимает галочку — этому не уйдёт. */
      row.insertBefore(tickMark(), ava);
      row.classList.toggle('is-picked', state.skip.indexOf(person.id) < 0);
      row.addEventListener('click', function () { toggleSkip(person.id, row); });
    } else if (state.picking && !person.banned) {
      row.insertBefore(tickMark(), ava);
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

  function tickMark() {
    var mark = document.createElement('span');
    mark.className = 'tick';
    return mark;
  }

  /* Снять галочку у человека из группы — и вернуть обратно. */
  function toggleSkip(id, row) {
    var at = state.skip.indexOf(id);
    if (at > -1) { state.skip.splice(at, 1); } else { state.skip.push(id); }
    if (row) { row.classList.toggle('is-picked', at > -1); }
    drawPicked();
  }

  function togglePick(id, row) {
    // Галочка и группа — разные способы выбрать: отметили человека
    // руками, значит группу больше не шлём.
    if (state.scope) { pickScope(state.scope); }
    var at = state.picked.indexOf(id);
    if (at > -1) { state.picked.splice(at, 1); } else { state.picked.push(id); }
    if (row) { row.classList.toggle('is-picked', at < 0); }
    drawPicked();
  }

  function drawPicked() {
    var bar = $('picked');
    bar.hidden = !state.picking;
    drawAlready();
    $('picked-count').textContent = state.scope
      ? state.scopeTitle + ': ' + manyCount() + ' чел.'
        + (state.skip.length ? ' (сняли ' + state.skip.length + ')' : '')
      : 'Выбрано: ' + state.picked.length;
    $('picked-write').disabled = manyCount() === 0 && state.picked.length === 0;
  }

  /* Кому из показанных уже писали руками за сутки: бот сам такие
     сообщения не пишет, в переписке они только от команды и от службы
     заботы. Значит, это и есть «уже отправили». */
  var DAY = 24 * 3600;

  function alreadyWritten() {
    if (!state.picking || !state.scope) { return []; }
    var since = Date.now() / 1000 - DAY;
    return state.rows.filter(function (person) {
      return person.last_side === 'out' && person.last_at > since
        && state.skip.indexOf(person.id) < 0;
    }).map(function (person) { return person.id; });
  }

  function drawAlready() {
    var ids = alreadyWritten();
    var button = $('already');
    button.hidden = !ids.length;
    button.textContent = 'Снять галочки у тех, кому уже писали: ' + ids.length;
  }

  function dropAlready() {
    var ids = alreadyWritten();
    if (!ids.length) { return; }
    state.skip = state.skip.concat(ids);
    drawPeople(state.rows.slice(), false);   // перерисовать галочки на месте
    drawPicked();
    toast('Сняли: ' + ids.length + '. Им сообщение не уйдёт.');
  }

  function setPicking(on) {
    state.picking = on;
    if (!on) { state.picked = []; clearScope(); }
    $('pick').classList.toggle('is-on', on);
    $('pick').textContent = on ? 'Отмена' : 'Выбрать';
    $('scopes').hidden = !on;
    if (!on) { $('already').hidden = true; }
    if (on) { loadScopes(); }
    drawPicked();
    loadPeople();
  }

  // ------------------------------------------------------- выбор группы
  //
  // AleX 23.09.2026: «пусть сверху будет галочка выбрать всех, и чтобы
  // действительно здесь все были в списке… или допустим захочется
  // оповестить всех, кто: (и выбрать шаг: первый/второй/третий/четвёртый/
  // купить)». Группу считает бот — здесь только кнопки.

  /* Счёт спрашиваем каждый раз: пульт держат открытым весь день, а
     воронка за день уходит вперёд. Прошлые числа показываем сразу, чтобы
     кнопки не мигали, и тихо обновляем. */
  function loadScopes() {
    if (state.scopes) { drawScopes(); }
    api('scopes', {})
      .then(function (data) { state.scopes = data.scopes || []; drawScopes(); })
      .catch(function () { /* без групп пульт всё равно работает */ });
  }

  function drawScopes() {
    var box = $('scopes');
    box.textContent = '';
    (state.scopes || []).forEach(function (item) {
      var chip = document.createElement('button');
      chip.type = 'button';
      chip.className = 'chip' + (state.scope === item.key ? ' is-on' : '');
      chip.disabled = !item.count;
      chip.textContent = item.title + ' · ' + item.count;
      chip.addEventListener('click', function () { pickScope(item.key); });
      box.appendChild(chip);
    });
  }

  /* Скольким уйдёт следующее сообщение: выбранным галочками или целой
     группе. Ноль — одному человеку, чей диалог открыт. Считается в одном
     месте, иначе счёт группы переживает её отмену. */
  function manyCount() {
    if (state.to.length) { return state.to.length; }
    if (!state.scope) { return 0; }
    return Math.max(0, state.scopeCount - state.skip.length);
  }

  function clearScope() {
    state.scope = '';
    state.scopeTitle = '';
    state.scopeCount = 0;
    state.skip = [];
  }

  function pickScope(key) {
    var item = (state.scopes || []).filter(function (s) { return s.key === key; })[0];
    if (!item || !item.count) { return; }
    var same = state.scope === key;
    clearScope();
    if (!same) {
      state.scope = key;
      state.scopeTitle = item.title;
      state.scopeCount = item.count;
      // Группа и отметки поимённо не складываются: иначе непонятно, кому уйдёт.
      state.picked = [];
    }
    drawScopes();
    drawPicked();
    loadPeople();
  }

  function drawPeople(people, append) {
    var box = $('people');
    if (!append) {
      box.textContent = '';
      state.loaded = 0;
      // Переписка копится с 18.09.2026: пока её нет, показываем всех и
      // говорим об этом, чтобы пустой список не выглядел поломкой.
      if (state.everyone) {
        var note = document.createElement('p');
        note.className = 'hint';
        note.textContent = 'Переписок пока нет — они копятся с того дня, как включили пульт. '
          + 'Ниже все, кто заходил в бота: откройте любого и напишите первым.';
        box.appendChild(note);
      }
    }
    var old = box.querySelector('.more');
    if (old) { box.removeChild(old); }          // кнопка всегда последняя
    people.forEach(function (person) { box.appendChild(personRow(person)); });
    if (state.more) { box.appendChild(moreRow()); }
    state.rows = append ? state.rows.concat(people) : people.slice();
    state.loaded += people.length;
    $('empty').hidden = state.loaded > 0;
    drawAlready();
    var waiting = people.filter(function (p) { return p.waiting; }).length;
    // В шапке тесно: четыре кнопки и счётчик. Поэтому коротко — сколько
    // ждут ответа, а иначе сколько показано из скольких.
    $('count').textContent = waiting ? waiting + ' ждут'
      : (state.more ? state.loaded + '/' + state.total : state.total + ' чел.');
  }

  /* «Показать ещё» — последней строкой списка. Строим заново на каждую
     отрисовку: список перед ней очищается целиком, и один и тот же узел
     во второй раз уже не нашёлся бы. */
  function moreRow() {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'more';
    button.textContent = 'Показать ещё · осталось ' + state.more;
    button.addEventListener('click', function () {
      button.disabled = true;
      button.textContent = 'Гружу…';
      loadPeople(true);
    });
    return button;
  }

  function loadPeople(add) {
    var offset = add ? state.offset : 0;
    return api('people', { query: state.query, offset: offset, scope: state.scope,
                           onlyChats: state.onlyChats && !state.everyone })
      .then(function (data) {
        var people = data.people || [];
        if (!add) { state.everyone = !!data.everyone; }
        state.offset = offset + people.length;
        state.total = data.total || people.length;
        state.more = data.more || 0;
        drawPeople(people, add);
      })
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
    if (message.file) { row.appendChild(fileTools(message)); }

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

  /* ВЛОЖЕНИЕ ОТ ЧЕЛОВЕКА (AleX 21.09.2026: «человек скинул какой-то файл
     в переписке с ботом, как посмотреть что это?»).

     Картинку — фото или скриншот, присланный файлом, — показываем прямо в
     диалоге. Всё остальное бот присылает вам в личку: там телеграм откроет
     что угодно и любого размера. Кнопки видны сразу, без нажатия на
     сообщение: пузырь «📎 файл» выглядел законченным, и догадаться, что на
     него можно нажать, было нельзя. */
  var VIEWABLE = { photo: true, document: true };

  function fileTools(message) {
    var box = document.createElement('div');
    box.className = 'msg__tools';
    if (VIEWABLE[message.kind]) {
      box.appendChild(toolButton('Открыть', function (button) {
        showFile(message, box, button);
      }));
    }
    box.appendChild(toolButton(VIEWABLE[message.kind] ? 'Мне в Telegram' : 'Прислать мне в Telegram',
      function (button) { sendFileToMe(message, button); }));
    return box;
  }

  function toolButton(title, onClick) {
    var button = document.createElement('button');
    button.type = 'button';
    button.className = 'msg__tool';
    button.textContent = title;
    button.addEventListener('click', function (event) {
      event.stopPropagation();
      if (button.disabled) { return; }
      onClick(button);
    });
    return button;
  }

  /* Картинку забираем через POST и показываем из памяти (blob): подпись
     Telegram, по которой пульт пускает, в адрес ссылки не попадает. */
  function showFile(message, box, button) {
    button.disabled = true;
    button.textContent = 'Загружаю…';
    fetch('/api/file', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ initData: initData, id: message.id, how: 'view' })
    }).then(function (res) {
      if (res.status === 415) {
        // Не картинка — документ, архив, pdf: такое откроет только телеграм.
        button.remove();
        return sendFileToMe(message, null, 'Это не картинка — прислал вам в чат с ботом.');
      }
      if (!res.ok) {
        return res.json().catch(function () { return {}; }).then(function (data) {
          throw new Error(data.error || ('ошибка ' + res.status));
        });
      }
      return res.blob().then(function (blob) {
        var img = document.createElement('img');
        img.className = 'msg__img';
        img.alt = 'вложение';
        img.src = URL.createObjectURL(blob);
        box.parentNode.insertBefore(img, box);
        button.remove();
      });
    }).catch(function (err) {
      button.disabled = false;
      button.textContent = 'Открыть';
      toast(err.message);
    });
  }

  function sendFileToMe(message, button, done) {
    if (button) { button.disabled = true; button.textContent = 'Отправляю…'; }
    return api('file', { id: message.id, how: 'me' })
      .then(function () {
        toast(done || 'Прислал вам в чат с ботом — откройте там.');
        if (button) { button.textContent = 'Прислал ✓'; }
      })
      .catch(function (err) {
        toast(err.message);
        if (button) {
          button.disabled = false;
          button.textContent = 'Прислать мне в Telegram';
        }
      });
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
    if (!state.picked.length && !state.scope) { return; }
    state.to = state.scope ? [] : state.picked.slice();
    state.id = null;
    state.person = null;
    listScreen.classList.add('is-behind');
    chatScreen.classList.add('is-open');
    stopEdit();
    $('card').hidden = true;
    $('info').hidden = true;
    $('ban').hidden = true;
    $('chat-name').textContent = state.scope ? state.scopeTitle
      : 'Выбрано: ' + state.to.length;
    $('chat-sub').textContent = state.scope
      ? manyCount() + ' чел. — каждому отдельно'
      : 'напишем каждому одно и то же';
    $('log').textContent = '';
    var hint = document.createElement('p');
    hint.className = 'hint';
    hint.textContent = state.scope
      ? 'Сообщение придёт каждому отдельно, как обычное сообщение от бота — '
        + 'с уведомлением. Сначала бот пришлёт его вам: так видно, что ушло людям. '
        + 'Когда закончит — отчитается, сколько дошло.'
      : 'Сообщение придёт каждому из выбранных отдельно, '
        + 'как обычное сообщение от бота — с уведомлением. '
        + 'Переписку видно в карточке каждого.';
    $('log').appendChild(hint);
    $('go').disabled = false;
    if (tg && tg.BackButton) { tg.BackButton.show(); }
  }

  function openChat(id) {
    state.to = [];
    clearScope();
    dropFile();
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
    clearScope();
    dropFile();
    chatScreen.classList.remove('is-open');
    listScreen.classList.remove('is-behind');
    if (tg && tg.BackButton) { tg.BackButton.hide(); }
    loadPeople();
  }

  /* Группе — только после подтверждения: кнопка «Написать» рядом, а
     уйдёт это сотням людей, и обратно уже не соберёшь. */
  function confirmGroup(next) {
    if (!state.scope) { return next(); }
    var ask = 'Отправить группе «' + state.scopeTitle + '» — '
      + manyCount() + ' чел.?'
      + (state.skip.length ? ' Снятым (' + state.skip.length + ') не уйдёт.' : '');
    if (tg && tg.showConfirm) { tg.showConfirm(ask, function (ok) { if (ok) { next(); } }); }
    else if (window.confirm(ask)) { next(); }
  }

  function send(event) {
    event.preventDefault();
    var text = $('text').value.trim();
    var media = $('media').value.trim();
    if (state.busy || (!state.id && !manyCount())) { return; }
    if (state.file) { return confirmGroup(sendFile); }
    if (!text && !media) { return; }
    confirmGroup(function () { reallySend(text, media); });
  }

  function reallySend(text, media) {
    var field = $('text');
    var link = $('media');
    var many = manyCount();
    state.busy = true;
    $('go').disabled = true;
    var call = state.editing
      ? api('edit', { messageId: state.editing, text: text })
      : api('send', { id: state.id, ids: state.to, scope: state.scope,
                      except: state.skip, text: text, media: media });
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
    // Группе шлёт не пульт, а рассылка: она идёт минутами и в конце
    // отчитывается в боте — здесь говорим только, что пошла.
    if (data.started) {
      toast('Рассылка пошла: ' + data.count + ' чел., примерно '
            + data.minutes + ' мин. Отчёт придёт в бота.');
      closeChat();
      setPicking(false);
      return;
    }
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
    var many = manyCount();
    var form = new FormData();
    form.append('initData', initData);
    form.append('id', String(state.id || 0));
    if (state.to.length) { form.append('ids', state.to.join(',')); }
    if (state.scope) { form.append('scope', state.scope); }
    if (state.skip.length) { form.append('except', state.skip.join(',')); }
    form.append('kind', kind);
    form.append('text', $('text').value.trim());
    form.append('file', blob, kind === 'note' ? 'note.webm' : 'voice.webm');
    state.busy = true;
    $('go').disabled = true;
    toast(kind === 'note' ? 'Отправляю кружок…' : 'Отправляю голосовое…');
    post('record', form)
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

  // ------------------------------------------------- файл с устройства
  //
  // AleX 23.09.2026: «нужно иметь возможность прикрепить и картинку с
  // устройства, и видео с устройства, и аудио если потребуется». Файл
  // уходит той же дорогой, что запись: одним запросом с подписью.

  var MAX_FILE_MB = 45;               // столько Телеграм берёт от бота

  function sizeOf(bytes) {
    return bytes >= 1024 * 1024 ? (bytes / 1024 / 1024).toFixed(1) + ' МБ'
                                : Math.max(1, Math.round(bytes / 1024)) + ' КБ';
  }

  function chooseFile(file) {
    if (!file) { return; }
    if (file.size > MAX_FILE_MB * 1024 * 1024) {
      toast('Файл тяжелее ' + MAX_FILE_MB + ' МБ — столько Телеграм от бота не берёт. '
            + 'Пришлите ссылкой по кнопке 📎.');
      return;
    }
    state.file = file;
    $('attach').hidden = false;
    $('attach-name').textContent = (file.name || 'файл') + ' · ' + sizeOf(file.size);
    $('pin').classList.add('is-on');
    $('text').focus();
  }

  function dropFile() {
    state.file = null;
    $('file').value = '';
    $('attach').hidden = true;
    $('pin').classList.remove('is-on');
  }

  function sendFile() {
    var file = state.file;
    if (!file || state.busy) { return; }
    var many = manyCount();
    var form = new FormData();
    form.append('initData', initData);
    form.append('id', String(state.id || 0));
    if (state.to.length) { form.append('ids', state.to.join(',')); }
    if (state.scope) { form.append('scope', state.scope); }
    if (state.skip.length) { form.append('except', state.skip.join(',')); }
    // kind и mime — раньше самого файла: по ним бот понимает, сколько
    // можно принять и чем это отправлять.
    form.append('kind', 'file');
    form.append('mime', file.type || '');
    form.append('text', $('text').value.trim());
    form.append('file', file, file.name || 'file');
    state.busy = true;
    $('go').disabled = true;
    toast('Отправляю файл…', true);
    post('record', form, function (percent) {
      toast(percent < 100 ? 'Отправляю файл… ' + percent + '%'
                          : 'Файл ушёл боту, он рассылает…', true);
    })
      .then(function (data) {
        $('text').value = '';
        grow($('text'));
        dropFile();
        if (many) { doneMany(data); return; }
        toast('Отправлено');
        drawChat({ person: state.person, card: state.card, chats: state.chats,
                   messages: data.messages || [] });
      })
      .catch(function (err) { toast(err.message); })
      .then(function () { state.busy = false; $('go').disabled = false; });
  }

  $('pin').addEventListener('click', function () { $('file').click(); });
  $('file').addEventListener('change', function () { chooseFile(this.files[0]); });
  $('attach-drop').addEventListener('click', dropFile);

  $('mic').addEventListener('click', function () { startRecord('voice'); });
  $('cam').addEventListener('click', function () { startRecord('note'); });
  $('rec-stop').addEventListener('click', function () { finishRecord(true); });
  $('rec-drop').addEventListener('click', function () { finishRecord(false); });

  // -------------------------------------------------------------- связи

  $('back').addEventListener('click', closeChat);
  $('pick').addEventListener('click', function () { setPicking(!state.picking); });
  drawPicked();                      // полоса выбора закрыта, «Написать» погашено
  $('picked-clear').addEventListener('click', function () { setPicking(false); });
  $('already').addEventListener('click', dropAlready);
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
      state.everyone = false;
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
    } else if (!state.picking && state.offset <= PAGE) {
      // Пока выбирают получателей или догрузили вторую сотню, список не
      // трогаем: он переставился бы прямо под пальцем.
      loadPeople();
    }
  }, REFRESH_MS);

  // Кнопка «Ответить в пульте» под уведомлением открывает сразу диалог:
  // адрес пульта приходит с ?id= того человека, о котором уведомление.
  loadPeople();
  var wanted = parseInt(new URLSearchParams(location.search).get('id'), 10);
  if (wanted) { openChat(wanted); }
}());
