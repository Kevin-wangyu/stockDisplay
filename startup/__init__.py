"""startup 包：服务启动的各个独立步骤。

serve.py 负责编排调用顺序。
"""
from startup.cookie import update_cookie_in_config, prompt_and_reload
from startup.integrity import check_data_integrity
from startup.archive import maybe_rebuild_month_archive
from startup.data_ready import ensure_data_ready