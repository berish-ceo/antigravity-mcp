# Antigravity MCP for Codex

Локальный MCP-сервер для работы Codex с установленным Antigravity CLI (`agy`). В репозитории также находится skill для исследования внешних источников. Сервер использует авторизованную сессию `agy`; отдельный API-ключ для этого моста не нужен.

## Установка агентом

Скопируйте готовый запрос из [INSTALL_WITH_AGENT.md](INSTALL_WITH_AGENT.md) в задачу Codex. Агент установит сервер и skill, проверит подключение и дополнит выбранный `AGENTS.md` правилами только для research. По умолчанию он использует `~/.codex/AGENTS.md`; проектный путь можно указать в запросе.

## Установка вручную

Нужны macOS, Python 3.10+, Codex и авторизованный `agy` с доступной Gemini Flash 3.8+. Сервер не устанавливает и не распространяет `agy`. Для иных ОС совместимость ещё не проверена.

```bash
git clone https://github.com/berish-ceo/antigravity-mcp.git "$HOME/.local/share/antigravity-mcp"
repo_dir="$HOME/.local/share/antigravity-mcp"
python3 -m venv "$repo_dir/.venv"
"$repo_dir/.venv/bin/python" -m pip install -r "$repo_dir/requirements.txt"
codex mcp add antigravity -- \
  "$repo_dir/.venv/bin/python" "$repo_dir/antigravity_mcp_server.py"
mkdir -p "$HOME/.codex/skills"
cp -R "$repo_dir/skills/antigravity-research" "$HOME/.codex/skills/"
```

Если запись `antigravity` или каталог skill уже существуют, проверьте их перед изменением. Для стандартного бюджета операции 300 секунд задайте `tool_timeout_sec = 330` в `[mcp_servers.antigravity]` файла `~/.codex/config.toml`. Для маршрутизации research добавьте [блок из инструкции агенту](INSTALL_WITH_AGENT.md#блок-для-agentsmd) в выбранный `AGENTS.md`. После изменения конфигурации откройте новую сессию Codex.

Команда `codex mcp add` и таблица `[mcp_servers.<name>]` описаны в [документации Codex MCP](https://developers.openai.com/codex/mcp). Пользовательские skills и установка из GitHub описаны в [документации Codex Skills](https://developers.openai.com/codex/skills).

После обновления перезапустите подключение MCP, чтобы загрузить новый Python-код. Turns и их stream events сохраняются в
`~/.gemini/antigravity-cli/mcp_runs.db`. Каждый turn выполняется отдельным `managed_worker.py`, который переживает
завершение MCP и сам сохраняет результат. Новый MCP читает тот же `turn_id` из SQLite. Закрытие транспорта не останавливает
исполнителя; для остановки используйте `antigravity_turn_interrupt`. Он проверяет identity процесса и ждёт подтверждения.
Приватные запросы и логи находятся в `~/.gemini/antigravity-cli/mcp-runs/<run_id>/`; запрос удаляется после загрузки.

## Таймауты одноразовых операций

`grounded_search`, `fetch_url`, `deep_research`, `grounded_dialogue`, `ask_antigravity` и `review`
принимают `timeout=300`. Отсчёт включает выбор модели, запуск CLI, получение данных и подготовку ответа.
Остановка процесса после deadline может занять ещё до 2 секунд. Отмена вызова завершает его дочерний CLI.

Если вызываете операцию с бюджетом больше 300 секунд, увеличьте `tool_timeout_sec` выше переданного `timeout` с запасом на остановку процесса. Максимальному `timeout=86400` соответствует клиентский лимит не менее 86402 секунд.

Пример аргументов: `{"url":"https://example.com","prompt":"Кратко изложи назначение страницы","timeout":450}`.

## Проверка

Из корня репозитория, в созданном виртуальном окружении:

```bash
.venv/bin/python -m unittest discover -s tests -q
```

## Основной lifecycle

Публичная модель повторяет Codex: **session → turn → items/commands**.

1. `antigravity_turn_start(message, ...)` сразу возвращает `turn_id`.
2. `antigravity_turn_wait(turn_id)` пассивно ждёт terminal state. Таймаут MCP-клиента не отменяет turn.
3. `antigravity_turn_items` и `antigravity_turn_commands` возвращают пагинированные stream events.
4. Следующий `turn_start(..., session_id=<id>)` продолжает контекст; `turn_interrupt` отменяет активный turn.

`execution_timeout_seconds` ограничивает сам `agy` turn и не равен таймауту вызова MCP. Допустимый диапазон — от 1
секунды до 24 часов. `idempotency_key` безопасно дедуплицирует повторный start. Prompt передаётся через stdin NDJSON и
не попадает в argv процесса. Legacy `antigravity_subagent_*` сохранены для совместимости.

Внешняя граница управления — целый turn. Параметр `agent` выбирает зарегистрированного `agy` agent. Внутренние
child-agent tools Antigravity видны в `antigravity_turn_commands`, но `agy 1.2.2` не предоставляет стабильных внешних
child IDs или отдельных `send/wait/interrupt` команд. MCP не имитирует отсутствующий control API.

Для `sync_to_desktop=true` откройте Antigravity Desktop. MCP передаёт исходный CLI summary методам Desktop
`LoadTrajectory` и `JetboxWriteSummary`; Desktop обновляет собственный реестр и уведомляет sidebar.
Прямая запись `agyhub_summaries_proto.pb` не обновляет список в памяти и больше не используется.
`synced_to_desktop=true` означает регистрацию и совпадение числа шагов CLI и Desktop, а `desktop_present` — только наличие файла базы.
Если Desktop закрыт или недоступен, выполнение CLI сохраняет свой результат, а синхронизация возвращает `false`.
После открытия Desktop повторите её через `antigravity_sync_all_desktop_sessions`.

Проверено на Antigravity Desktop 2.12.2/macOS: новая MCP-сессия появляется без reload и открывается с сообщениями.
Обнаружение HTTPS-порта использует `~/Library/Logs/Antigravity/language_server.log`; локальный API версионно зависим.
Desktop кэширует уже открытые разговоры: последующие CLI turns могут не обновить их сообщения даже после reload UI.
В этом случае MCP возвращает `synced_to_desktop=false`, сохраняя успешный результат CLI. API `LoadTrajectory` не сбрасывает
загруженный кэш; полное live-обновление продолжений через CLI пока не поддерживается.
Не продолжайте один разговор одновременно в CLI и Desktop.

---

## 7 Ключевых Правил Работы

1. **Grounded Search — основной способ поиска**: `antigravity_grounded_search` используется как первичный инструмент для поиска превью-информации и актуальных фактов в интернете.
2. **Таймаут**: параметр `timeout=300` задаёт общий бюджет одноразовой операции в секундах (1–86400). Antigravity восстанавливается после промежуточных ошибок внутри бюджета. `FALLBACK_REQUIRED` возвращается только при итоговом отказе или таймауте.
3. **Агентная загрузка URL в `antigravity_fetch_url`**: агент загружает страницу через браузер или ридер и возвращает выжимку с короткими подтверждающими фрагментами.
4. **Ответ по запросу**: Antigravity загружает и проверяет страницу, затем отвечает на `prompt` или готовит summary. Тело страницы возвращается только если оно требуется по `prompt`.
5. **Экономия времени и токенов**: Запрещены избыточные и дублирующие запросы. Запросы должны быть точечными и сфокусированными.
6. **Модели по умолчанию: Gemini Flash 3.8+ Low**: Все остальные модели (Pro, не-Flash) строго запрещены. Уровень мышления (effort) — всегда `low`, повышение до `medium` разрешено только в критических ситуациях.
7. **Модели для суб-агента**: Для managed turns разрешено использовать модели семейства Gemini Flash 3.8+ с уровнями `low`, `medium`, `high`.

---

## Доступные инструменты:

1. **`antigravity_list_models(filter_type?, refresh?)`**
   * Динамический реестр моделей Gemini Flash 3.8+ (все остальные модели запрещены).
   * Возвращает дефолтную модель (`gemini-3.8-flash-low` или 3.9+), уровни мышления и правила использования.

2. **`antigravity_grounded_search(query, require_sources?, depth?, domains?, output_format?, timeout?)`**
   * Основной инструмент поиска с Google Grounding.
   * После сбоя `search_web` Antigravity может получить веб-источники другим доступным способом и раскрывает замену в ответе.
   * Модель по умолчанию: Gemini Flash Low.

3. **`antigravity_fetch_url(url, prompt?, model?, timeout?)`**
   * Ответ по `prompt` или summary по умолчанию; HTML, полный текст и конкретные фрагменты включаются только если нужны запросу.
   * При неполной загрузке Antigravity может отрендерить страницу, дочитать фрагменты или проверить официальный источник. При итоговом отказе возвращается `FALLBACK_REQUIRED`.

4. **`antigravity_deep_research(topic, aspects?, domains?, require_sources?, timeout?)`**
   * Архитектурное исследование с рассуждениями на базе Gemini Flash Medium. `timeout=300`, 1–86400 секунд.

5. **`antigravity_grounded_dialogue(query, require_sources?, conversation_id?, depth?, domains?, timeout?)`**
   * Итеративный поиск с сохранением контекста диалога. `timeout=300`, 1–86400 секунд.

6. **`antigravity_turn_start(message, project_dir?, session_id?, mode?, model?, effort?, execution_timeout_seconds?, sync_to_desktop?, agent?, idempotency_key?, result_schema?)`**
   * Асинхронно создаёт durable turn. Возвращает `turn_id`; `session_id` появляется после stream-события `init`.
   * Разрешены модели Gemini Flash 3.8+ с effort: `low` (дефолт), `medium`, `high`.

7. **`antigravity_turn_wait/status/interrupt(turn_id)`**
   * Ожидание, моментальный snapshot и отмена одного turn.

8. **`antigravity_turn_items/commands(turn_id, cursor?, limit?)`**
   * Наблюдаемые сообщения, reasoning/tool updates и результаты; `commands` оставляет только tool lifecycle.

9. **`antigravity_session_list/read(...)`**
   * Инвентарь session/turn и чтение метаданных одной session.

10. **`antigravity_subagent_*`**
   * Совместимые legacy aliases для start/wait/status/continue/stop/run.

11. **`antigravity_archive_session(conversation_id)`**
   * Архивирует Desktop artifacts и сохраняет resumable CLI conversation.

12. **`ask_antigravity(prompt, context?, model?, effort?, timeout?)`**
   * Быстрые консультации на базе Gemini Flash Low.

13. **`antigravity_review(code_or_diff, focus_areas?, timeout?)`**
   * Код-ревью на базе Gemini Flash Medium.

14. **`antigravity_solve_in_project(task, project_dir?, model?)`**
   * Синоним для `antigravity_subagent_run` с текстовым ответом.

## Лицензия

Код и документация этого репозитория опубликованы под [MIT](LICENSE). Antigravity CLI (`agy`) устанавливается отдельно и не входит в репозиторий.
