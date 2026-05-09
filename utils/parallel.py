# utils/parallel.py
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed
from typing import List, Callable, Any, Optional, Union


def run_parallel(
        process_func: Callable[[Any], Optional[dict]],
        items: List[Any],
        max_workers: int = 4,
        executor_type: str = "thread",  # "thread" 或 "process"
        description: str = "",  # 可选，用于日志
        collect_results: bool = True,
) -> List[dict]:
    """
    通用并行执行器。

    :param process_func: 处理单个 item 的函数，返回结果字典或 None
    :param items: 待处理的数据列表
    :param max_workers: 最大并行数
    :param executor_type: 执行器类型，建议 I/O 密集用 thread，CPU 密集考虑 process
    :param description: 进度日志前缀
    :param collect_results: 是否收集返回值（False 时只执行，适用于副作用场景）
    :return: 成功项的列表（保持原始顺序？通常不需要严格顺序，但可以后续排序）
    """
    if not items:
        return []

    Executor = ThreadPoolExecutor if executor_type == "thread" else ProcessPoolExecutor

    results = []
    with Executor(max_workers=max_workers) as executor:
        futures = {executor.submit(process_func, item): item for item in items}

        for future in as_completed(futures):
            item = futures[future]
            try:
                res = future.result()
                if collect_results and res is not None:
                    results.append(res)
            except Exception as e:
                # 这里可以记录日志，但不中断整体流程
                print(f"[并行错误] 处理 {item} 时出错: {e}")
            # 若需要进度条，可以在这里更新

    return results