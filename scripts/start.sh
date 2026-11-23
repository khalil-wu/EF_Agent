#!/usr/bin/env bash
# EFAgent launcher for macOS/Linux: builds the frontend bundle and gateway when stale,
# then starts the worker and gateway (or reuses healthy ones).
set -euo pipefail

repo=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
no_browser=0
config=""
command="start"
doctor_args=()
while [ $# -gt 0 ]; do
    case "$1" in
        start|stop|status|doctor) command="$1";;
        --no-browser) no_browser=1;;
        --json|--strict) doctor_args+=("$1");;
        --config) config="${2:?--config 需要一个文件路径}"; shift;;
        *) echo "未知参数：$1" >&2; exit 2;;
    esac
    shift
done

if [ -z "$config" ] && [ -f "$repo/scripts/settings.local.sh" ]; then
    config="$repo/scripts/settings.local.sh"
fi
if [ -n "$config" ]; then
    # shellcheck disable=SC1090
    . "$config"
fi

# EFAGENT_* 是本机配置名，运行部件只读 EEGAGENT_*。
for name in $(compgen -v 'EFAGENT_' || true); do
    export "EEGAGENT_${name#EFAGENT_}=${!name}"
done

export EEGAGENT_STORAGE_DIR="${EEGAGENT_STORAGE_DIR:-$repo/storage}"
export EEGAGENT_SKILLS_DIR="${EEGAGENT_SKILLS_DIR:-$repo/skills}"
export EEGAGENT_DATA_DIRS="${EEGAGENT_DATA_DIRS:-$repo/data}"
export EEGAGENT_KNOWLEDGE_DIR="${EEGAGENT_KNOWLEDGE_DIR:-$repo/knowledge}"
export EEGAGENT_MODEL_REGISTRY="${EEGAGENT_MODEL_REGISTRY:-$repo/configs/model_registry.yaml}"
export EEGAGENT_LLM_CAPABILITIES="${EEGAGENT_LLM_CAPABILITIES:-$repo/configs/conversation_models.json}"
export EEGAGENT_WORKER_URL="${EEGAGENT_WORKER_URL:-http://127.0.0.1:8100}"
export EEGAGENT_ADDR="${EEGAGENT_ADDR:-127.0.0.1:8080}"
if [ -z "${EEGAGENT_PYTHON:-}" ]; then
    if [ -x "$repo/.venv-macos/bin/python" ]; then
        EEGAGENT_PYTHON="$repo/.venv-macos/bin/python"
    else
        EEGAGENT_PYTHON="$repo/.venv/bin/python"
    fi
    export EEGAGENT_PYTHON
fi
export EEGAGENT_SOURCE_ROOT="$repo"
export PYTHONPATH="$repo/src${PYTHONPATH:+:$PYTHONPATH}"
unset PYTHONSTARTUP || true
if [ -z "${EEGAGENT_LOCAL_FONTS:-}" ] && [ -f "$repo/frontend/bridge/fonts.local.css" ]; then
    export EEGAGENT_LOCAL_FONTS="$repo/frontend/bridge/fonts.local.css"
fi

logs="$EEGAGENT_STORAGE_DIR/logs"
worker_hostport="${EEGAGENT_WORKER_URL#*://}"; worker_hostport="${worker_hostport%%/*}"
worker_host="${worker_hostport%%:*}"; worker_port="${worker_hostport##*:}"
addr_host="${EEGAGENT_ADDR%%:*}"; addr_port="${EEGAGENT_ADDR##*:}"
case "$addr_host" in ""|0.0.0.0|"::"|"[::]") addr_host="127.0.0.1";; esac
gateway_url="http://$addr_host:$addr_port"
static_dir="$repo/gateway/internal/server/static"

health() { curl --noproxy '*' -fsS "$1/health" 2>/dev/null; }

mtime() { stat -f %m "$1" 2>/dev/null || stat -c %Y "$1"; }

stop_service() {
    local name="$1"
    if [ -f "$logs/$name.pid" ]; then
        local pid
        pid=$(cat "$logs/$name.pid")
        if kill -0 "$pid" 2>/dev/null; then
            kill "$pid" 2>/dev/null || true
        fi
        rm -f "$logs/$name.pid"
    fi
}

case "$command" in
    stop)
        stop_service gateway
        stop_service worker
        echo "已停止"
        exit 0;;
    status)
        for service in "worker:$EEGAGENT_WORKER_URL" "gateway:$gateway_url"; do
            name="${service%%:*}"; url="${service#*:}"
            echo "== $name =="
            health "$url" || echo "未运行"
            echo
        done
        exit 0;;
    doctor)
        exec "$EEGAGENT_PYTHON" -m eegagent.doctor ${doctor_args[@]+"${doctor_args[@]}"};;
esac

if [ -z "${EEGAGENT_LLM_BASE_URL:-}" ] || [ -z "${EEGAGENT_LLM_MODEL:-}" ]; then
    echo "缺少模型配置：请设置 EFAGENT_LLM_BASE_URL 与 EFAGENT_LLM_MODEL（如需密钥再加 EFAGENT_LLM_API_KEY），" >&2
    echo "写入 scripts/settings.local.sh 后可复用（参考 scripts/settings.local.sh.example），或直接导出后重试。" >&2
    exit 1
fi

# (a) 前端：源码新于产物时重建
bundle="$static_dir/transcript-ui.js"
if command -v npm >/dev/null 2>&1 && { [ ! -f "$bundle" ] || [ -n "$(find "$repo/frontend/src" "$repo/frontend/bridge" "$repo/frontend/scripts" -type f -newer "$bundle" -print -quit 2>/dev/null)" ]; }; then
    npm --prefix "$repo/frontend" run build
fi

# (b) 网关：二进制缺失或落后于源码时重建
mkdir -p "$EEGAGENT_STORAGE_DIR/bin" "$logs"
platform=$(uname -s | tr 'A-Z' 'a-z')
binary="$EEGAGENT_STORAGE_DIR/bin/gateway-$platform"
if [ ! -x "$binary" ] || [ -n "$(find "$repo/gateway" -path "$repo/gateway/output" -prune -o -type f -newer "$binary" -print -quit 2>/dev/null)" ]; then
    (cd "$repo/gateway" && go build -buildvcs=false -o "$binary" ./cmd/gateway)
fi

# (c) 本地字体 UI 覆盖层
if [ -n "${EEGAGENT_LOCAL_FONTS:-}" ]; then
    ui="$EEGAGENT_STORAGE_DIR/ui"
    mkdir -p "$ui"
    cp -R "$static_dir/." "$ui/"
    cat "$EEGAGENT_LOCAL_FONTS" >> "$ui/app.css"
    fonts_dir="$(dirname "$EEGAGENT_LOCAL_FONTS")/fonts"
    if [ -d "$fonts_dir" ]; then
        cp -R "$fonts_dir" "$ui/"
    fi
    export EEGAGENT_STATIC_DIR="$ui"
fi

# (d)
export EEGAGENT_GATEWAY_BUILD_ID="gateway-$(mtime "$binary")"
export EEGAGENT_WORKER_BUILD_ID="worker-$("$EEGAGENT_PYTHON" -c 'import os,sys;print(max(os.stat(os.path.join(d,f)).st_mtime_ns for d,_,fs in os.walk(sys.argv[1]) for f in fs if f.endswith(".py")))' "$repo/src")"

config_ids=$("$EEGAGENT_PYTHON" - <<'PY'
import hashlib, json, os, re
from pathlib import Path
e = os.environ
stamp = lambda name: Path(e[name]).stat().st_mtime_ns if e.get(name) and Path(e[name]).exists() else 0
retrieval = {k: e[k] for k in sorted(e) if re.match(r'^EEGAGENT_(EMBED_|RERANK_|OCR(?:_|$))', k)}
worker = [e['EEGAGENT_STORAGE_DIR'], e['EEGAGENT_DATA_DIRS'], e['EEGAGENT_KNOWLEDGE_DIR'], e['EEGAGENT_MODEL_REGISTRY'], stamp('EEGAGENT_MODEL_REGISTRY'), e['EEGAGENT_WORKER_URL'], e['EEGAGENT_PYTHON'], retrieval]
gateway = [e['EEGAGENT_STORAGE_DIR'], e['EEGAGENT_SKILLS_DIR'], e['EEGAGENT_WORKER_URL'], *[e.get('EEGAGENT_LLM_'+k, '') for k in ('BASE_URL','MODEL','API_KEY','PROXY')], e['EEGAGENT_LLM_CAPABILITIES'], stamp('EEGAGENT_LLM_CAPABILITIES'), e.get('EEGAGENT_LOCAL_FONTS',''), stamp('EEGAGENT_LOCAL_FONTS')]
for value in (worker, gateway):
    print(hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest())
PY
)
export EEGAGENT_WORKER_CONFIG_ID="$(printf '%s\n' "$config_ids" | sed -n '1p')"
export EEGAGENT_GATEWAY_CONFIG_ID="$(printf '%s\n' "$config_ids" | sed -n '2p')"

service_build_id() { health "$1" | "$EEGAGENT_PYTHON" -c 'import json,sys;print(json.load(sys.stdin).get("build_id",""))' 2>/dev/null || true; }
service_config_id() { health "$1" | "$EEGAGENT_PYTHON" -c 'import json,sys;print(json.load(sys.stdin).get("configuration_id",""))' 2>/dev/null || true; }

sessions_running() { curl --noproxy '*' -fsS "$gateway_url/sessions" 2>/dev/null | grep -q '"running":true'; }

service_action() {
    ACTION=start
    health "$2" >/dev/null || return 0
    if [ "$(service_build_id "$2")" = "$3" ] && [ "$(service_config_id "$2")" = "$4" ]; then
        ACTION=reuse; echo "$1 已在运行，复用"; return
    fi
    if sessions_running; then
        ACTION=reuse; echo "有分析正在运行，保留当前进程；分析结束后重新运行本命令以更新"; return
    fi
    if [ -f "$logs/$1.pid" ] && kill -0 "$(cat "$logs/$1.pid")" 2>/dev/null; then
        ACTION=restart; return
    fi
    ACTION=reuse; echo "$1 由其他进程启动且版本不同，未替换"
}

wait_down() { for _ in $(seq 1 "$2"); do health "$1" >/dev/null || return 0; sleep 1; done; }

service_action gateway "$gateway_url" "$EEGAGENT_GATEWAY_BUILD_ID" "$EEGAGENT_GATEWAY_CONFIG_ID"; gateway_action=$ACTION
service_action worker "$EEGAGENT_WORKER_URL" "$EEGAGENT_WORKER_BUILD_ID" "$EEGAGENT_WORKER_CONFIG_ID"; worker_action=$ACTION

if [ "$gateway_action" = restart ]; then stop_service gateway; wait_down "$gateway_url" 10; fi
if [ "$worker_action" = restart ]; then stop_service worker; wait_down "$EEGAGENT_WORKER_URL" 10; fi

# (e) worker
if [ "$worker_action" != reuse ]; then
    (cd "$repo" || exit 1; nohup "$EEGAGENT_PYTHON" -m uvicorn eegagent.worker.app:app --host "$worker_host" --port "$worker_port" --app-dir "$repo/src" >> "$logs/worker.log" 2>&1 & echo $! > "$logs/worker.pid")
    ready=0
    for _ in $(seq 1 90); do
        if health "$EEGAGENT_WORKER_URL" >/dev/null; then ready=1; break; fi
        sleep 1
    done
    if [ "$ready" -ne 1 ]; then
        echo "worker 未在 90 秒内就绪，日志末尾：" >&2
        tail -n 30 "$logs/worker.log" >&2 || true
        exit 1
    fi
fi

# (f) gateway
if [ "$gateway_action" != reuse ]; then
    (cd "$repo/gateway" || exit 1; nohup "$binary" >> "$logs/gateway.log" 2>&1 & echo $! > "$logs/gateway.pid")
    ready=0
    for _ in $(seq 1 30); do
        if health "$gateway_url" >/dev/null; then ready=1; break; fi
        sleep 1
    done
    if [ "$ready" -ne 1 ]; then
        echo "gateway 未在 30 秒内就绪，日志末尾：" >&2
        tail -n 30 "$logs/gateway.log" >&2 || true
        exit 1
    fi
fi

# (g)
echo "EFAgent：$gateway_url/"
echo "代码：${repo}；日志：${logs}"
if [ "$no_browser" -eq 0 ] && [ "$(uname -s)" = "Darwin" ]; then
    open "$gateway_url/"
fi
