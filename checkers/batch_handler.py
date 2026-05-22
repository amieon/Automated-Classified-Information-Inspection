# batch_handlers.py
import json
from checkers.db_checker import DBConnector, DBCheckerModule

async def handle_check_batch(
    module_instances: dict,
    url_configs_json: str,
    db_configs_json: str,
    file_path: str,
    image_path: str,
    audio_path: str,
    algorithm: str,
    keywords: str,
    max_insert: int,
):
    """执行批量检查，返回 (full_html, full_text)"""
    url_configs = json.loads(url_configs_json) if url_configs_json else []
    db_configs = json.loads(db_configs_json) if db_configs_json else []

    html_parts = []
    full_text = ""

    # ---- 网页 ----
    if url_configs and "web" in module_instances:
        html_parts.append("<h3>🌐 网页批量检查</h3><hr>")
        web_checker = module_instances["web"]
        for cfg in url_configs:
            url = cfg.get("url", "").strip()
            if not url:
                continue
            try:
                h, t, _ = await web_checker.check_single_url(
                    url, algorithm, keywords, max_insert
                )
                html_parts.append(f"<h5>🔗 {url}</h5><hr>{h}")
                full_text += f"URL: {url}\n{t}\n{'-'*40}\n"
            except Exception as e:
                html_parts.append(
                    f"<div class='alert alert-danger'>❌ {url} 失败: {e}</div>"
                )
                full_text += f"URL: {url} 失败: {e}\n"

    # ---- 数据库 ----
    if db_configs and "db" in module_instances:
        html_parts.append('<h3 class="mt-4">🗄️ 数据库批量检查</h3><hr>')
        db_checker = DBCheckerModule()

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
                            results = db_checker._parallel_scan(
                                tables=tables, keywords=keywords,
                                algorithm=algorithm, max_insert=max_insert,
                                **conn_kwargs,
                            )
                            db_info = f"{db_type} {host}:{port}/{db_name}"
                            html_block = db_checker._build_html_result(
                                results, tables, len(tables), db_path=db_info
                            )
                            text_block = db_checker._generate_text_report(
                                results, tables, len(tables), f"数据库 #{idx}: {db_info}"
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
                    results = db_checker._parallel_scan(
                        tables=tables, keywords=keywords,
                        algorithm=algorithm, max_insert=max_insert,
                        **conn_kwargs,
                    )
                    db_info = f"{db_type} {host}:{port}/{database}"
                    html_block = db_checker._build_html_result(
                        results, tables, len(tables), db_path=db_info
                    )
                    text_block = db_checker._generate_text_report(
                        results, tables, len(tables), f"数据库 #{idx}: {db_info}"
                    )
                    html_parts.append(
                        f"<h5>🗄️ 数据库 #{idx}：{db_info}</h5><hr>{html_block}"
                    )
                    full_text += text_block + "\n\n"
            finally:
                connector.disconnect()

    # ---- 文件/图片/音频路径 ----
    path_tasks = [
        ("file", file_path, "📁 文件路径检查"),
        ("image", image_path, "🖼️ 图片路径检查"),
        ("audio", audio_path, "🎵 音频路径检查"),
    ]
    for mod_key, path_val, title in path_tasks:
        if path_val and path_val.strip() and mod_key in module_instances:
            html_parts.append(f"<h3 class='mt-3'>{title}</h3><hr>")
            checker = module_instances[mod_key]
            try:
                h, t, _ = await checker.check_path(
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

    full_html = "".join(html_parts)
    return full_html, full_text


async def handle_db_scan_all(
    db_type: str,
    host: str,
    port: int,
    dbname: str,
    user: str,
    password: str,
    keywords: str,
    algorithm: str,
    max_insert: int,
):
    """扫描数据库所有库，返回 (full_html, full_text)"""
    html_parts = []
    full_text = ""

    conn_kwargs = dict(
        db_type=db_type, host=host, port=port or 3306,
        user=user, password=password, database=dbname,
    )

    connector = DBConnector(**conn_kwargs)
    if not connector.connect():
        html_parts.append("<div class='alert alert-danger'>❌ 数据库连接失败</div>")
        return "".join(html_parts), full_text

    try:
        databases = connector.get_databases()
        if not databases:
            html_parts.append("<div class='alert alert-warning'>⚠️ 连接成功，但未找到任何库</div>")
            return "".join(html_parts), full_text

        db_checker = DBCheckerModule()
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

                results = db_checker._parallel_scan(
                    tables=tables, keywords=keywords,
                    algorithm=algorithm, max_insert=max_insert,
                    **conn_kwargs,
                )
                db_info = f"{db_type} {host}:{port}/{db_name}"
                html_block = db_checker._build_html_result(
                    results, tables, len(tables), db_path=db_info
                )
                text_block = db_checker._generate_text_report(
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