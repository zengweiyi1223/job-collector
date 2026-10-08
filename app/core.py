"""Source identity and bounded collection for COLLECTOR-MVP-v0.1.1.

No browser/network/database actions are performed by this module.
"""

from dataclasses import dataclass, field
import re
from urllib.parse import urljoin, urlsplit


class InvalidJobLink(ValueError):
    pass


def job_identity(link: str) -> tuple[str, str]:
    """Use the public BOSS detail-link identifier, never a title or URL hash."""
    url = urlsplit(urljoin("https://www.zhipin.com/", link))
    if (url.scheme != "https" or url.hostname != "www.zhipin.com"
            or url.username or url.password or url.port not in (None, 443)):
        raise InvalidJobLink("不是允许的 BOSS 岗位详情链接")
    match = re.fullmatch(r"/job_detail/([A-Za-z0-9_-]+)\.html", url.path)
    if not match:
        raise InvalidJobLink("无法识别公开岗位链接标识")
    return "boss", match.group(1)


def public_job_url(link: str) -> str:
    _, job_id = job_identity(link)
    return f"https://www.zhipin.com/job_detail/{job_id}.html"


@dataclass
class CollectionBudget:
    """One manual run: <=3 observed batches and <=20 new detail attempts."""
    batches: int = 0
    detail_attempts: int = 0
    inserted: int = 0
    seen: set[tuple[str, str]] = field(default_factory=set)
    pending: set[tuple[str, str]] = field(default_factory=set)
    detail_limit: int = 20

    def __post_init__(self):
        if not 1 <= self.detail_limit <= 20:
            raise ValueError('详情预算只能在1～20之间')

    def begin_batch(self) -> bool:
        if self.batches >= 3 or self.detail_attempts >= self.detail_limit or self.inserted >= self.detail_limit:
            return False
        self.batches += 1
        return True

    def decide(self, link: str, exists: bool) -> str:
        identity = job_identity(link)
        if not self.batches:
            raise RuntimeError("需先记录来源列表批次")
        if identity in self.seen:
            return "duplicate_in_run"
        self.seen.add(identity)
        if exists:
            return "already_exists_not_checked"
        if self.detail_attempts >= self.detail_limit or self.inserted >= self.detail_limit:
            return "detail_budget_reached"
        self.detail_attempts += 1
        self.pending.add(identity)
        return "read_new_detail"

    def detail_finished(self, link: str, saved: bool) -> None:
        identity = job_identity(link)
        if identity not in self.pending:
            raise RuntimeError("详情未预约或已结束")
        self.pending.remove(identity)
        if saved:
            self.inserted += 1
