# checkers/batch_handler.py
import json
from typing import Tuple, Optional

from checkers.db_checker import DBConnector, DBCheckerModule


async def check_batch_core(
    url_configs_json: str,
    db_configs_json: str,
    file_path: Optional[str],
    image_path: Optional[str],
    audio_path: Optional[str],
    algorithm: str,
    keywords: str,
    max_insert: int,
    web_module_instance,
    file_module_instance,
    image_module_instance,
    audio_module_instance,
    db_module_instance,
) -> Tuple[str, str]:
    """
    执行批量全检，返回 (html_report, text_report)
    """
    html_parts = []
    full_text = ""

    # ---------- 网页 ----------
    url_configs = json.loads(url_configs_json) if url_configs_json else []
    if url_configs and web_module_instance:
        html_parts.append("<h3>🌐 网页批量检查</h3><hr>")
        for cfg in url_configs:
            url = cfg.get("url", "").strip()
            if not url:
                continue
            try:
                h, t, _ = await web_module_instance.check_single_url(
                    url, algorithm, keywords, max_insert
                )
                html_parts.append(f"<h5>🔗 {url}</h5><hr>{h}")
                full_text += f"URL: {url}\n{t}\n{'-'*40}\n"
            except Exception as e:
                html_parts.append(
                    f"<div class='alert alert-danger'>❌ {url} 失败: {e}</div>"
                )
                full_text += f"URL: {url} 失败: {e}\n"

    # ---------- 数据库 ----------
    db_configs = json.loads(db_configs_json) if db_configs_json else []
    if db_configs and db_module_instance:
        html_parts.append('<h3 class="mt-4">🗄️ 数据库批量检查</h3><hr>')
        for idx, cfg in enumerate(db_configs, 1):
            db_type = cfg.get("db_type", "mysql")
            host = cfg.get("host", "localhost")
            port = int(cfg.get("port", 3306))
            user = cfg.get("user", "")
            password = cfg.get("password", "")
            database = cfg.get("database") or cfg.get("dbname", "")
            scan_all = cfg.get("scan_all", False)

            conn_kwargs = dict(
                db_type=db_type, host=host, port=port,
                user=user, password=password, database=database,
            )
            connector = DBConnector(**conn_kwargs)
            if not connector.connect():
                html_parts.append(
                    f"<div class='alert alert-danger'>❌ 数据库 #{idx} 连接失败</div>"
                )
                continue
            try:
                if scan_all:
                    databases = connector.get_databases()
                    for db_name in databases:
                        conn_kwargs["database"] = db_name
                        connector2 = DBConnector(**conn_kwargs)
                        if not connector2.connect():
                            continue
                        try:
                            tables = connector2.get_tables()
                            if not tables:
                                continue
                            results = db_module_instance._parallel_scan(
                                tables=tables, keywords=keywords,
                                algorithm=algorithm, max_insert=max_insert,
                                **conn_kwargs,
                            )
                            db_info = f"{db_type} {host}:{port}/{db_name}"
                            html_block = db_module_instance._build_html_result(
                                results, tables, len(tables), db_path=db_info
                            )
                            text_block = db_module_instance._generate_text_report(
                                results, tables, len(tables),
                                f"数据库 #{idx}: {db_info}"
                            )
                            html_parts.append(
                                f"<h5>🗄️ {db_info}</h5><hr>{html_block}"
                            )
                            full_text += text_block + "\n\n"
                        finally:
                            connector2.disconnect()
                else:
                    tables = connector.get_tables()
                    if not tables:
                        html_parts.append(
                            f"<div class='alert alert-warning'>数据库 #{idx} 无表</div>"
                        )
                        continue
                    results = db_module_instance._parallel_scan(
                        tables=tables, keywords=keywords,
                        algorithm=algorithm, max_insert=max_insert,
                        **conn_kwargs,
                    )
                    db_info = f"{db_type} {host}:{port}/{database}"
                    html_block = db_module_instance._build_html_result(
                        results, tables, len(tables), db_path=db_info
                    )
                    text_block = db_module_instance._generate_text_report(
                        results, tables, len(tables),
                        f"数据库 #{idx}: {db_info}"
                    )
                    html_parts.append(
                        f"<h5>🗄️ 数据库 #{idx}：{db_info}</h5><hr>{html_block}"
                    )
                    full_text += text_block + "\n\n"
            finally:
                connector.disconnect()

    # ---------- 文件/图片/音频路径 ----------
    path_tasks = [
        ("file", file_path, "📁 文件路径检查"),
        ("image", image_path, "🖼️ 图片路径检查"),
        ("audio", audio_path, "🎵 音频路径检查"),
    ]
    for mod_key, path_val, title in path_tasks:
        if not path_val or not path_val.strip():
            continue
        instance = None
        if mod_key == "file":   instance = file_module_instance
        elif mod_key == "image": instance = image_module_instance
        elif mod_key == "audio": instance = audio_module_instance
        if not instance:
            continue
        html_parts.append(f"<h3 class='mt-3'>{title}</h3><hr>")
        try:
            # 要求模块实现 check_path 方法
            h, t, _ = await instance.check_path(
                path_val.strip(), algorithm, keywords, max_insert
            )
            html_parts.append(h)
            full_text += t + "\n"
        except AttributeError:
            html_parts.append(
                f"<div class='alert alert-warning'>{title} 模块未实现批量接口</div>"
            )
        except Exception as e:
            html_parts.append(
                f"<div class='alert alert-danger'>{title} 失败: {e}</div>"
            )
            full_text += f"{title} 失败: {e}\n"

    return "".join(html_parts), full_text


async def db_scan_all_core(
    db_type: str,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
    algorithm: str,
    keywords: str,
    max_insert: int,
    db_module_instance,
) -> Tuple[str, str]:
    """
    扫描指定数据库实例的所有库，返回 (html_report, text_report)
    """
    html_parts = []
    full_text = ""

    port = port or 3306
    conn_kwargs = dict(
        db_type=db_type, host=host, port=port,
        user=user, password=password, database=database,
    )

    connector = DBConnector(**conn_kwargs)
    if not connector.connect():
        return (
            "<div class='alert alert-danger'>❌ 数据库连接失败</div>",
            "数据库连接失败"
        )

    try:
        databases = connector.get_databases()
        if not databases:
            return (
                "<div class='alert alert-warning'>⚠️ 连接成功，但未找到任何库</div>",
                "未找到任何库"
            )

        all_db_count = len(databases)
        scanned_count = 0

        for db_name in databases:
            conn_kwargs["database"] = db_name
            connector2 = DBConnector(**conn_kwargs)
            if not connector2.connect():
                html_parts.append(
                    f"<div class='alert alert-warning'>⚠️ 库 {db_name} 连接失败，跳过</div>"
                )
                continue
            try:
                tables = connector2.get_tables()
                if not tables:
                    html_parts.append(
                        f"<div class='text-muted'>📭 库 <b>{db_name}</b>：无表</div>"
                    )
                    continue

                results = db_module_instance._parallel_scan(
                    tables=tables, keywords=keywords,
                    algorithm=algorithm, max_insert=max_insert,
                    **conn_kwargs,
                )
                db_info = f"{db_type} {host}:{port}/{db_name}"
                html_block = db_module_instance._build_html_result(
                    results, tables, len(tables), db_path=db_info
                )
                text_block = db_module_instance._generate_text_report(
                    results, tables, len(tables), f"数据库: {db_info}"
                )

                html_parts.append(
                    f"<h5 style='margin-top:1rem;'>🗄️ {db_info}（{len(tables)} 张表）</h5><hr>{html_block}"
                )
                full_text += text_block + "\n\n"
                scanned_count += 1
            finally:
                connector2.disconnect()

        summary = (
            f"<h4>🔍 扫描所有库完成：共 {all_db_count} 个库，"
            f"实际扫描 {scanned_count} 个库</h4><hr>"
        )
        full_html = summary + "".join(html_parts)
        return full_html, full_text

    finally:
        connector.disconnect()