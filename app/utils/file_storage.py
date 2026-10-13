"""上传文件的校验与落盘。

安全要点（三条，都是"不写就会出事"的）：
  ① **绝不使用客户端传来的文件名当磁盘路径**。文件名只存进数据库做展示，
     落盘名由 uuid 生成 —— 否则 ../../.env 这类文件名可以直接写到项目外。
  ② **边读边判大小**，不信 Content-Length。客户端可以谎报长度，
     也可以根本不带这个头；只在读的过程中累加才拦得住超大文件。
  ③ 按知识库分目录存，避免单目录堆积几万个文件（文件系统检索会变慢）。
"""

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from app.config import settings
from app.core.exceptions import AppException
from app.core.response import ErrorCode

logger = logging.getLogger(__name__)

READ_CHUNK = 1024 * 1024  # 每次读 1 MB

# documents.filename 列是 String(255)，长度必须在校验阶段就挡住：
# 真库开了 STRICT_TRANS_TABLES，超长会在 flush 时报 1406，
# 用户输入问题就会变成 59999 服务器内部错误（SQLite 不报错，测试抓不到）
FILENAME_MAX_LENGTH = 255


@dataclass(frozen=True)
class SavedFile:
    stored_name: str
    file_path: Path
    file_type: str
    file_size: int


def _extension_of(filename: str) -> str:
    """取小写、不带点的扩展名。先用 Path().name 剥掉任何目录成分。"""
    name = Path(filename or "").name
    return Path(name).suffix.lower().lstrip(".")


def display_filename(filename: str) -> str:
    """客户端文件名规范化：剥掉路径成分，只留展示用的最后一段。

    Service 存进 documents.filename 的必须是这个值 —— 直接存 upload.filename
    的话，"../../x.txt" 这种就会带着目录成分进数据库。
    """
    return Path(filename or "").name


def validate_filename(filename: str) -> str:
    """校验文件名，返回规范化的 file_type；不通过抛 40000。"""
    if not filename or not filename.strip():
        raise AppException(ErrorCode.PARAM_ERROR, "文件名不能为空")

    name = display_filename(filename)
    if len(name) > FILENAME_MAX_LENGTH:
        raise AppException(
            ErrorCode.PARAM_ERROR,
            f"文件名过长（{len(name)} 字符），最多 {FILENAME_MAX_LENGTH} 个字符",
        )

    ext = _extension_of(name)
    allowed = settings.allowed_extensions_set
    if ext not in allowed:
        raise AppException(
            ErrorCode.PARAM_ERROR,
            f"不支持的文件类型 .{ext or '?'}，仅支持：{', '.join(sorted(allowed))}",
        )
    return ext


async def save_upload(upload: UploadFile, knowledge_base_id: int) -> SavedFile:
    """把上传流写入磁盘，返回落盘信息。超限/空文件会清理掉半截文件再抛异常。"""
    file_type = validate_filename(upload.filename or "")

    target_dir = settings.upload_path / str(knowledge_base_id)
    target_dir.mkdir(parents=True, exist_ok=True)

    stored_name = f"{uuid.uuid4().hex}.{file_type}"
    target = target_dir / stored_name
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024

    size = 0
    try:
        with target.open("wb") as fh:
            while True:
                block = await upload.read(READ_CHUNK)
                if not block:
                    break
                size += len(block)
                if size > max_bytes:
                    raise AppException(
                        ErrorCode.PARAM_ERROR,
                        f"文件超过大小上限 {settings.MAX_UPLOAD_SIZE_MB} MB",
                    )
                fh.write(block)
    except AppException:
        target.unlink(missing_ok=True)
        raise
    except OSError as e:
        target.unlink(missing_ok=True)
        logger.exception("写入上传文件失败 path=%s", target)
        # ⚠️ **不要把 `e` 拼进 message**：`OSError` 的字符串里带着文件的**绝对路径**
        # （`[Errno 28] No space left on device: 'E:\\...\\uploads\\xxx.md'`），
        # 而 message 是**要回给客户端**的 —— 那等于把服务端的目录结构送给用户。
        # 原文已经进日志了（上面那行 exception），这里只给一句能看懂的话。
        # 口径与其余上游异常一致：**上游原文只进日志**。
        raise AppException(ErrorCode.FILE_ERROR, "文件保存失败，请稍后重试") from e
    finally:
        await upload.close()

    if size == 0:
        target.unlink(missing_ok=True)
        raise AppException(ErrorCode.PARAM_ERROR, "文件内容为空")

    logger.info(
        "文件已保存 kb_id=%s stored=%s type=%s size=%s",
        knowledge_base_id, stored_name, file_type, size,
    )
    return SavedFile(
        stored_name=stored_name, file_path=target, file_type=file_type, file_size=size
    )


def delete_file(file_path: str | Path) -> bool:
    """删除磁盘文件。失败只记日志不抛异常 —— 数据库记录已经删了，
    为了一个删不掉的文件把接口整成 500，对用户没有任何意义。"""
    path = Path(file_path)
    try:
        path.unlink(missing_ok=True)
        return True
    except OSError as e:
        logger.warning("删除文件失败 path=%s err=%s", path, e)
        return False


def delete_files(file_paths: list[str]) -> int:
    """批量删除磁盘文件（删知识库时连带清它的文档文件用）。返回成功删除的个数。"""
    deleted = 0
    for p in file_paths:
        if p and delete_file(p):
            deleted += 1
    return deleted


def remove_dir_if_empty(directory: Path) -> bool:
    """目录空了就删掉。知识库删完后残留的空 <uploads>/<kb_id>/ 目录没人回收。"""
    try:
        if directory.is_dir() and not any(directory.iterdir()):
            directory.rmdir()
            return True
    except OSError as e:
        logger.warning("删除空目录失败 dir=%s err=%s", directory, e)
    return False
