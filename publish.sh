#!/usr/bin/env bash
# Publish this prepared repository to the owner's GitHub account.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
repo="Odysseos/ha_wled_yaha"
if ! command -v gh >/dev/null 2>&1; then
    echo 'Нужна GitHub CLI: https://cli.github.com/' >&2
    echo 'Либо создай https://github.com/new и загрузи содержимое этой папки через Add file → Upload files.' >&2
    exit 1
fi
if ! gh auth status >/dev/null 2>&1; then
    gh auth login --web
fi
if [[ ! -d .git ]]; then
    git init -b main
fi
git add -- custom_components hacs.json README.md CHANGELOG.md LICENSE tests requirements-dev.txt .github .gitignore publish.sh
if ! git diff --cached --quiet; then
    git commit -m 'WLED YaHA 0.1.3: UI setup, multiple garlands and automatic YaHA export'
fi
if gh repo view "$repo" >/dev/null 2>&1; then
    remote_url="$(gh repo view "$repo" --json sshUrl --jq .sshUrl)"
    if git remote get-url origin >/dev/null 2>&1; then
        current_url="$(git remote get-url origin)"
        case "$current_url" in
            "https://github.com/$repo"|"https://github.com/$repo.git"|"git@github.com:$repo.git") ;;
            *) echo 'origin указывает на другой репозиторий. Публикация отменена.' >&2; exit 1 ;;
        esac
    else
        git remote add origin "$remote_url"
    fi
    # Normal push only: an existing remote history is never overwritten.
    git push -u origin HEAD:main
else
    gh repo create "$repo" --public --description 'WLED garlands and preset mapping for YaHA in Home Assistant' --source . --remote origin --push
fi
echo "Готово: https://github.com/$repo"
echo "HACS: https://my.home-assistant.io/redirect/hacs_repository/?owner=Odysseos&repository=ha_wled_yaha&category=integration"
