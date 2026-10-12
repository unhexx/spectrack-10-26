# Code review + security review — spectrack-10-26

- Дата: 2026-10-12
- Ветка: `review` (от `origin/dev`, 328e7d3)
- Метод: 6 (Deep Bug Hunt), 7 (Impact), 9 (Code Review), 10 (Security). Ручной разбор кода, тесты проекта, bandit, pip-audit; каждая находка HIGH и MEDIUM воспроизведена тестом.
- Объём: ~6 тыс. строк приложения (`product/app`, `product/matching`, `deploy/fsp`) + изменения dev относительно main

## Важно о ветках

Все 18 коммитов dev уже есть в main как дубли с другими хэшами (rebase или cherry-pick). Деревья `origin/main` и `origin/dev` отличаются только файлом `product/docs/prezentaciya.pptx`, удалённым в main. Поэтому всё ниже относится и к main.

## Гейты и проверка находок

Всё запускалось в образе `fsp-vacancies:local` (Python 3.12) с `--network none`. Образ собран офлайн в `deploy/fsp/wheels`.

| Гейт | Результат |
|------|-----------|
| Тесты проекта `pytest product/tests` | **178 passed**, 0 failed (39,7 с) |
| Воспроизведение находок [`reviews/test_review_repro.py`](test_review_repro.py) | **11 passed**: подтверждены все находки HIGH и MEDIUM (п. 1–7) |
| bandit 1.9.4 (вместо semgrep, которому для правил нужна сеть) | High 0, Medium 2, Low 14. Все разобраны, подтверждённых нет (см. ниже) |
| pip-audit | **5 уязвимостей в `cryptography 46.0.7`**, 1 в `pytest 8.4.2` (см. п. 8) |

Тесты проекта находки не ловят: они проверяют задуманное поведение. Например, `test_freshness_bonus_lifts_a_worse_rank_key_only_inside_the_window` прямо фиксирует, что бонус задания выше балла (п. 6).

Перезапуск воспроизведения (тест проходит, **пока проблема есть**, и после исправлений должен падать):
```
docker run --rm --network none -v "$PWD/reviews/test_review_repro.py:/srv/product/tests/test_review_repro.py:ro" \
  -e PRODUCT_SECRET=test-secret-32-characters-minimum!! --entrypoint pytest fsp-vacancies:local \
  product/tests/test_review_repro.py -q -rA
```

---

## HIGH

### 1. ФСП ID подделывается вручную, OIDC-привязку можно обойти
- `product/app/services/profiles.py:194-202`, `product/app/web/candidate.py:65`, `product/app/templates/candidate/cabinet.html:137`
- Поле `external_id` свободно редактируется в анкете (форма и `PUT /api/candidate/profile`). Кандидат вписывает чужой ID участника и получает:
  - чужие достижения и разряд на своей карточке (`categories.py:173-181`);
  - рост `rank_key`: число достижений и признак призового места (`categories.py:38-45`);
  - уникальный индекс `uq_profiles_external` закрепляет ID за ним. Настоящий участник потом получает «external_id_taken» при честной привязке через Keycloak (`fsp_link.py:77-82`).
- Сопутствующий баг: `PUT /api/candidate/profile` без ключа `external_id` стирает привязанный ID (`profiles.py:195`, `None` → `None`).
- **Подтверждено:** `test_p1_external_id_is_typed_by_hand_and_squats_the_real_owner` (чужие достижения получены, у второго профиля 409) и `test_p1_api_save_without_key_wipes_linked_id`.
- **Исправление:** убрать `external_id` из формы и API анкеты. Писать его только в `/auth/fsp/callback`. При сохранении анкеты оставлять текущее значение.

### 2. Подтверждение почты ничего не подтверждает
- `product/app/api/auth.py:53`, `:87`; `product/app/web/auth_pages.py:69`
- `POST /api/auth/register` и `POST /api/auth/confirm/resend` возвращают код в ответе. `resend` отдаёт свежий код для **любого** неподтверждённого адреса. Значит, кто угодно регистрируется на чужую почту. Именно эта почта потом уходит работодателю после «принято» (`vacancies.py:90`, `offers.py:55`).
- Для демо-стенда так задумано («код на стенде»), но режим не выключается флагом. При выкладке за пределы стенда это дыра.
- **Подтверждено:** `test_p2_resend_hands_out_code_for_someone_elses_email`. Посторонний без сессии получил код для чужого адреса и подтвердил учётку (200).
- **Исправление:** отдавать код только при `PRODUCT_STAND_CODES=1`. В остальных случаях отправлять письмом. Ограничить частоту `resend`.

## MEDIUM

### 3. Скрытые достижения утекают через `rank_key` (внесено в dev: 0d19bb6, c07120e)
- `product/app/services/categories.py:382`
- Карточка в `GET /api/employer/candidates` отдаёт `rank_key` как есть: `confirmed-score-COUNT-PRIZE-id`. При `show_achievements=False` работодатель всё равно видит число достижений и признак призового места. А `neighbor_phrase` эти поля как раз старательно прячет.
- Фильтр `has_fsp=true|false` тоже работает по скрытым достижениям (`need.py:98-101`), так что их наличие можно перебрать.
- **Подтверждено:** `test_p3_hidden_achievements_leak_through_rank_key_and_filter`. На карточке `achievements: []` и `fsp_rank: ""`, а в `rank_key` `…-0005-1-…`: 5 достижений и призовое место. Фильтр `has_fsp=true` карточку находит, `has_fsp=false` — нет.
- **Исправление:** не отдавать `rank_key` наружу или маскировать поля 3–4. Для скрытых достижений не применять `has_fsp`, либо явно задокументировать, что фильтр видит скрытое.

### 4. Скрытый стаж ищется и подсвечивается в выдаче (внесено в dev)
- `product/app/services/categories.py:182-189`, `product/matching/need.py:123-124`
- `haystack` включает `experience` независимо от `show_experience`. Причина в карточке печатает совпавшие стемы («в тексте профиля: …»). Работодатель перебирает слова в `q` и восстанавливает скрытый стаж.
- **Подтверждено:** `test_p4_hidden_experience_is_searchable`. Стаж «kubernetes в банке» скрыт, на карточке `experience: ""`, но по `q=kubernetes` карточка находится, а в `list_reason` печатается стем `kubernet`.
- **Исправление:** при `consent.show_experience == 0` передавать `experience=""` в `haystack_text`.

### 5. Числовой ответ теста роняет запрос с 500 (внесено в dev: b0067d9)
- `product/matching/assessment.py:112-124`, функция `_canonical_int`
- `text.lstrip("-").isdigit()` пропускает строки, на которых `int()` падает с `ValueError`:
  - `"--5"` (после `lstrip` остаётся `"5"`);
  - `"²"`, `"①"` (`isdigit` даёт True для надстрочных и обведённых цифр);
  - строка длиннее 4300 цифр (лимит `int_max_str_digits` в Python 3.12).
- `ValueError` — не `ApiError`, поэтому `POST /candidate/test/answer` (поле `value`) и `POST /api/candidate/tests/...` отвечают 500.
- **Подтверждено:** `test_p5_numeric_normalizer_raises[--5|²|5000 цифр]` (все три дают `ValueError`) и `test_p5_numeric_answer_is_unhandled_through_api`: `POST /api/candidate/tests/{id}/submit` с ответом `"--5"` падает необработанным исключением.
- **Исправление:** `re.fullmatch(r"[+-]?[0-9]{1,9}", text)` или `try/except ValueError → None`.

### 6. Бонус периодического задания выдаётся без выполнения задания
- `product/app/services/periodic.py:229-236`, `product/app/services/categories.py:313`
- `POST /api/candidate/periodic-task` ставит `completed_at` без ответа, и это даёт `freshness_bonus = 1`. Бонус стоит в ключе сортировки **перед** `rank_key`, где лежат подтверждение грейда и балл теста. Кандидат без теста одним кликом обходит кандидата с подтверждённым грейдом и баллом 100 при равном совпадении с потребностью.
- **Подтверждено:** `test_p6_one_click_completion_outranks_confirmed_score`. Кандидат без теста после пустого `POST /api/candidate/periodic-task` стоит в выдаче выше кандидата с подтверждённым баллом 100.
- **Исправление:** засчитывать бонус только при содержательной сдаче (ответ, который проверяет `answer_accepted`, как в отклике), либо ставить бонус после `confirmed`/`score`.

### 7. Разбор PDF блокирует весь сервер
- `product/app/api/resumes.py:82-88`, `product/app/web/candidate.py:122-131`, `product/matching/resume.py:111-114`, `deploy/fsp/entrypoint.sh` (`--workers 1`)
- Обработчик объявлен `async def`, а синхронный `apply_resume` (pypdf `extract_text` по всем страницам, SQLite) вызывается прямо в event loop. Число страниц не ограничено. PDF на 2 МБ с тысячами страниц или тяжёлыми потоками занимает единственный воркер на секунды или минуты, и сервис встаёт для всех. Хватает любого кандидата, а регистрация открыта (см. п. 2).
- **Подтверждено:** `test_p7_pdf_parse_runs_on_event_loop_and_is_slow`. Оба обработчика — корутины без `run_in_threadpool`. PDF на 1,89 МБ (в пределах лимита 2 МБ) с 4000 страниц разбирается **2,8 с**, и всё это время сервер с одним воркером не отвечает никому. Параллельные загрузки складываются.
- **Исправление:** `await run_in_threadpool(apply_resume, ...)` и ограничение `len(reader.pages) <= 10`.

### 8. Уязвимые зависимости, и закреплённый диапазон не даёт их обновить
- `product/pyproject.toml` (`cryptography>=44,<47`), `product/requirements.lock`, `deploy/fsp/Dockerfile:18,20`
- pip-audit ([`reviews/pip-audit.md`](pip-audit.md)):
  - `cryptography 46.0.7`: GHSA-537c-gmf6-5ccf (исправлено в 48.0.1), PYSEC-2026-3553 и PYSEC-2026-3554 (49.0.0), PYSEC-2026-3552 (50.0.0);
  - `pytest 8.4.2`: PYSEC-2026-1845 (9.0.3).
- В приложении `cryptography` проверяет RS256-подпись `id_token` (`oidc.py:145-149`). Степень влияния на этот путь нужно сверить по описаниям в OSV, без сети я их не читал. Обновиться в любом случае нельзя: мешает верхняя граница `<47`.
- `pytest` и `httpx` попадают в **рабочий** образ: Dockerfile ставит `"/srv/product[dev]"`, и те же колёса нужны контейнеру `app`.
- **Исправление:** `cryptography>=50,<51`, пересобрать `requirements.lock` и прогнать `test_oidc.py`. В рабочем образе ставить `/srv/product` без `[dev]`, а для тестов собирать отдельную стадию.

## Разбор bandit

Каталог `product/build/lib` — копия кода, которую `pip` оставляет при сборке образа. Ниже только уникальные срабатывания.

| Правило | Где | Вердикт |
|---------|-----|---------|
| B310 urlopen (Medium) | `oidc.py:128` | Ложное срабатывание. URL собирается из `OIDC_INTERNAL_ISSUER` (окружение). `_rewritten` всегда подставляет внутренний issuer, адрес из ответа discovery наружу не идёт. |
| B704 Markup (Medium) | `render.py:41` | Ложное срабатывание. Текст сначала проходит `escape`, а в ссылку попадает только совпадение `[A-Za-z0-9_/-]`. |
| B105 hardcoded password | `api/auth.py`, `secret.py`, `oidc.py` | Ложное срабатывание: примеры OpenAPI (`password-ok`) и имена переменных. |
| B311 random | `assessment.py:63` и симуляции | Допустимо. В продукте генератор засеивается из `secrets.randbits(64)`, остальное — офлайн-симуляции. |
| B101 assert | `db/session.py:71`, `synth_eval.py` | Допустимо: инварианты, не проверка входа. |

## LOW / укрепление

| # | Где | Что |
|---|-----|-----|
| 9 | `periodic.py:99-108` | `_pick` = `profile_id % len(rows)`: пока набор вакансий не меняется, кандидат каждые 30 дней получает одно и то же задание. |
| 10 | `periodic.py:139-145`, `vacancies.py:187-189` | Если работодатель снял задание или вакансию, открытое предложение «мертвеет», и новое появится только через 30 дней после `offered_at`. |
| 11 | `services/auth.py:143-180` | Блокировка входа считается по почте: пятью неверными паролями можно заблокировать любого пользователя на 15 минут. Ограничения по IP нет. Два параллельных первых промаха вставляют `LoginThrottle` дважды → `IntegrityError` → 500. |
| 12 | `services/tests.py:439-465` | `answer_question` читает, меняет и пишет `form_json` без блокировки. Два параллельных ответа могут потерять один ответ или дважды закрыть этап. |
| 13 | `main.py:43`, `compose.yaml` | JSON API и multipart `/api/candidate/resume` защищены от CSRF только `SameSite=Lax`. Флаг `Secure` по умолчанию выключен. Сессия — подписанная cookie на 7 дней, выход не отзывает украденную cookie. |
| 14 | `deploy/fsp/fsp-realm.json`, `compose.yaml` | Keycloak в `start-dev`, `admin/admin`, `sslRequired: none`, `bruteForceProtected: false`. Порты привязаны к 127.0.0.1, для стенда допустимо, в прод нельзя. |
| 15 | `api/profiles.py:141`, `web/candidate.py:47` | GET с побочными эффектами: создаёт и удаляет строки `periodic_tasks`. |
| 16 | `api/resumes.py:60-61` | Пишет в приватные `request._body` и `_stream_consumed` Starlette 1.7: может сломаться при обновлении. |
| 17 | `categories.py:148-159` | N+1: 2–5 запросов на каждый профиль при каждой выдаче. Для «десятков пользователей» терпимо. |

## Что сделано хорошо

- Argon2id с параметрами OWASP. Код подтверждения хранится хэшем. Пароли и коды не попадают в лог.
- Защита от фиксации сессии: при входе вызывается `session.clear()`. CSRF на всех формах сравнивается через `compare_digest`.
- OIDC: PKCE S256, `state`, RS256 с выбором ключа по `kid`, проверка `iss`/`aud`/`exp`/`nbf`. Учётки из токена не создаются.
- IDOR закрыт везде: чужие вакансии, отклики, приглашения и попытки теста отдают 404. Смена статусов идёт условным `UPDATE` без гонок.
- Ключ теста не уходит клиенту (`public_questions`). В шаблонах нет `|safe`. Секреты не закоммичены, у демо нет пароля по умолчанию.

## Приоритет исправлений

1. п. 1 и п. 3–4: целостность и приватность выдачи, на этом держится продукт.
2. п. 5 и п. 7: 500 и отказ в обслуживании от одного запроса.
3. п. 8: обновить `cryptography` и убрать `[dev]` из рабочего образа.
4. п. 6: честность ранжирования.
5. п. 2: флаг стенда до любой выкладки за пределы демо.

Готовность к исправлениям: после каждой правки `reviews/test_review_repro.py` по соответствующему пункту должен **падать**, а `product/tests` — оставаться зелёным. Тесты, которые сейчас закрепляют ошибочное поведение (п. 6), нужно поменять вместе с кодом.
