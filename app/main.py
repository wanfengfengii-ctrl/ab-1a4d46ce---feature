"""FastAPI 入口：事故导排网络检修审计 / 薄弱管段复核业务 API 与静态页面。"""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .flow import NetworkValidationError, audit_network, review_network

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(
    title="化工园区事故导排网络检修审计",
    version=__version__,
    description=(
        "录入泄压源、安全焚烧端、汇合节点与带方向/容量/检修标记的管段，"
        "在正常网络及每条可检修管段临时失效后的残余网络上独立求最大流，"
        "判定事故持续排出流量是否始终可达；审计通过后可发起薄弱管段复核，"
        "识别在全部同容量最小割中均不可绕开的管段。"
    ),
)


@app.exception_handler(NetworkValidationError)
async def _on_validation_error(_: Request, exc: NetworkValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"error": exc.message, "field": exc.field},
    )


@app.get("/health")
async def health() -> dict:
    """容器健康检查端点。"""
    return {"status": "ok", "service": "flare-audit", "version": __version__}


async def _draft_payload(request: Request):
    """解析草稿 JSON；非法请求体返回 (None, 400 响应)。"""
    try:
        payload = await request.json()
    except Exception:
        return None, JSONResponse(status_code=400, content={"error": "请求体必须是合法 JSON", "field": None})
    if not isinstance(payload, dict):
        return None, JSONResponse(status_code=400, content={"error": "请求体必须是 JSON 对象", "field": None})
    return payload, None


@app.post("/api/audit")
async def audit(request: Request) -> dict:
    """对一份导排网络草稿执行检修审计。

    正常网络与每个单管段移除情景**独立**计算最大流；全部达标才放行。
    方向、容量、节点引用等业务输入无效时返回 400。
    """
    payload, err = await _draft_payload(request)
    if err is not None:
        return err

    result = audit_network(
        source=payload.get("source"),
        sink=payload.get("sink"),
        nodes=payload.get("nodes", []),
        edges=payload.get("edges", []),
        required_flow=payload.get("required_flow"),
    )
    result["service"] = "flare-audit"
    result["version"] = __version__
    return result


@app.post("/api/review")
async def review(request: Request) -> dict:
    """对一份导排网络草稿执行薄弱管段复核。

    服务端从完整草稿**重新执行既有审计**；全部情形达标时，逐情形给出
    每条管段在全部源侧最小割中的归属分级（从不 / 部分 / 全部跨越）、
    最小割容量与相对事故要求流量的裕量；任一情形本已不达标时，保留
    既有首条失败证据，不生成薄弱分级。输入无效时返回 400。
    """
    payload, err = await _draft_payload(request)
    if err is not None:
        return err

    result = review_network(
        source=payload.get("source"),
        sink=payload.get("sink"),
        nodes=payload.get("nodes", []),
        edges=payload.get("edges", []),
        required_flow=payload.get("required_flow"),
    )
    result["service"] = "flare-audit"
    result["version"] = __version__
    return result


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
