# Обновление Antigravity MCP и skill на Mac

Запрос для Codex: «Обнови мой установленный Antigravity MCP и skill из `berish-ceo/antigravity-mcp` по этой инструкции. Сохрани мои изменения и проверь все четыре инструмента. Мой прокси уже выдан / мне нужен режим без прокси».

1. Проверьте `agy --version` и вход в личный аккаунт. Найдите установленный сервер (`codex mcp get antigravity`) и его каталог. Если в существующем клоне нет собственных правок и `origin` указывает на этот репозиторий, выполните там `git pull --ff-only origin main`. Для новой установки клонируйте репозиторий по [README](README.md). Не заменяйте изменённую или неуправляемую копию: сначала сохраните её или используйте отдельный каталог. Предыдущая версия с управляемыми заданиями доступна в истории Git; закончите такие задания перед заменой.
2. В обновлённом клоне создайте `.venv` на Python 3.13 и установите `requirements.txt`. Проверьте `.venv/bin/python -m unittest discover -s tests -q`. Каталоги `workspaces` с их скрытыми `.agents/hooks.json` и файл `tool_guard.py` обязательны. Не копируйте только серверный Python-файл.
3. Обновите skill из того же клона. Следующие команды сохраняют старый skill в приватной резервной папке вне каталога загружаемых skills:

```sh
skills_dir="${CODEX_HOME:-$HOME/.codex}/skills"
mkdir -p "$skills_dir"
if [ -e "$skills_dir/antigravity-research" ]; then
  backup=$(mktemp -d "${TMPDIR:-/tmp}/agy-skill-backup.XXXXXX")
  cp -R "$skills_dir/antigravity-research" "$backup/"
  printf 'Предыдущий skill сохранён: %s\n' "$backup"
fi
mkdir -p "$skills_dir/antigravity-research/references"
cp skills/antigravity-research/SKILL.md "$skills_dir/antigravity-research/"
cp skills/antigravity-research/references/*.md "$skills_dir/antigravity-research/references/"
```

4. Проверьте, что в `~/.codex/config.toml` ровно одна запись `[mcp_servers.antigravity]`: `command` — абсолютный путь к Python из обновлённого клона, `args` содержит абсолютный путь к его `antigravity_mcp_server.py`. В самой таблице задайте `tool_timeout_sec = 960` и `supports_parallel_tool_calls = true`. Сохраните остальные настройки и политику согласований. Для нового сервера без прокси подходит `codex mcp add` из README; для персонального прокси выполните [шаг подключения](docs/proxy-client.md).
5. Перезапустите Codex Desktop. В списке инструментов должны быть `antigravity_grounded_search`, `antigravity_fetch_url`, `antigravity_codebase_scout` и `antigravity_docs_research`. Попросите выполнить поиск с официальной ссылкой и исследовать тестовый локальный каталог. Ошибка `FALLBACK_REQUIRED` не считается успешной проверкой. Текущий skill не требует менять глобальные правила Codex для других задач.
