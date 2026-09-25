"""Bounded, fail-closed public errors. Never forward arbitrary upstream text.

An upstream message may echo a prompt or credential even inside valid JSON.
Only recognized diagnostic categories are exposed; unknown bodies remain private.
"""
import json
import re

MAX_ERROR_BYTES = 8192


def public_upstream_error(error, request_id):
    message = "上游拒绝了请求；未识别的错误详情已隐藏，请凭请求编号排查。"
    code = "upstream_error"
    try:
        raw = error.read(MAX_ERROR_BYTES + 1)
        if len(raw) <= MAX_ERROR_BYTES:
            value = json.loads(raw)
            detail = value.get("error") if isinstance(value, dict) else None
            text = detail.get("message") if isinstance(detail, dict) else None
            # Full matching is deliberate: do not forward appended prompt/key data.
            if isinstance(text, str) and re.fullmatch(
                r"[A-Za-z0-9_.-]{1,80} is not a multimodal model[.!]?", text.strip()
            ):
                message = "该模型不支持图片输入（not a multimodal model）；请在客户端声明文本输入能力，或使用已验证支持图片的模型。"
                code = "unsupported_image_input"
    except (ValueError, OSError, TypeError):
        pass
    finally:
        error.close()
    if code == "upstream_error":
        message = {
            401: "上游认证失败；请检查中转站的上游密钥。",
            403: "上游拒绝访问；请检查上游账号、模型权限或访问策略。",
            429: "上游限流或额度限制；请检查用量及并发限制。",
            502: "上游服务暂时异常。",
            503: "上游服务暂时不可用。",
            504: "上游服务响应超时。",
        }.get(error.code, message)
    return {"error": {"message": message, "type": "upstream_error", "code": code},
            "request_id": request_id}
