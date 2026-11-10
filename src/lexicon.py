"""Diplomatic trigger lexicon (Vietnamese + English) for the 11 extracted relation types.

Used in two places:
  * preprocess.py  - keep only sentences that contain at least one trigger
  * rule baseline  - candidate relation types for a sentence (tier A, before the LLM)
"""
from __future__ import annotations

import re

RELATIONS = [
    "MET_WITH", "PHONE_CALL", "VISITED", "SIGNED_AGREEMENT", "UPGRADED_PARTNERSHIP",
    "MILITARY_AID", "ECONOMIC_AID", "SANCTIONED", "EXPELLED_DIPLOMAT", "CONDEMNED", "SUPPORTED",
]

_VI = {
    "MET_WITH": r"hội đàm|hội kiến|tiếp kiến|gặp gỡ|gặp|cuộc gặp|thượng đỉnh|hội nghị|"
                r"tiếp (?:Tổng thống|Thủ tướng|Ngoại trưởng|Bộ trưởng|Đại sứ|Chủ tịch|Quốc vương|Tổng thư ký|đoàn)",
    "PHONE_CALL": r"điện đàm|cuộc gọi|qua điện thoại|hội đàm trực tuyến",
    "VISITED": r"thăm chính thức|thăm cấp nhà nước|chuyến thăm|thăm làm việc|công du|đến thăm|sang thăm",
    "SIGNED_AGREEMENT": r"ký kết|ký|thỏa thuận|hiệp định|hiệp ước|bản ghi nhớ|MoU|tuyên bố chung",
    "UPGRADED_PARTNERSHIP": r"nâng cấp quan hệ|đối tác chiến lược|đối tác toàn diện|thiết lập quan hệ ngoại giao|bình thường hóa quan hệ",
    "MILITARY_AID": r"viện trợ quân sự|hỗ trợ quân sự|viện trợ vũ khí|cung cấp vũ khí|gửi vũ khí|chuyển giao vũ khí|"
                    r"gói (?:viện trợ|hỗ trợ) an ninh|hệ thống phòng không",
    "ECONOMIC_AID": r"viện trợ|khoản vay|cho vay|tài trợ|hỗ trợ tài chính|viện trợ nhân đạo|gói hỗ trợ|khoản hỗ trợ",
    "SANCTIONED": r"trừng phạt|cấm vận|đóng băng tài sản|danh sách đen",
    "EXPELLED_DIPLOMAT": r"trục xuất|triệu hồi đại sứ|triệu tập đại sứ|cắt đứt quan hệ ngoại giao|nhân vật không được hoan nghênh|persona non grata",
    "CONDEMNED": r"lên án|chỉ trích|phản đối|cáo buộc|bác bỏ|cảnh báo|đe dọa|phản bác",
    "SUPPORTED": r"ủng hộ|hoan nghênh|hỗ trợ|cam kết|chúc mừng|cảm ơn|đánh giá cao|ca ngợi",
}

_EN = {
    "MET_WITH": r"met|meet|meets|meeting|talks|summit|hosted|received|sat down with",
    "PHONE_CALL": r"phone call|phoned|spoke by phone|telephone|call with|spoke with|video call",
    "VISITED": r"visit|visits|visited|visiting|trip to|state visit|arrived in|toured",
    "SIGNED_AGREEMENT": r"signed|signs|sign|signing|agreement|deal|treaty|accord|memorandum|pact|joint statement",
    "UPGRADED_PARTNERSHIP": r"strategic partnership|comprehensive partnership|upgraded (?:ties|relations)|"
                            r"elevated (?:ties|relations)|established diplomatic relations|normali[sz]ed? (?:ties|relations)",
    "MILITARY_AID": r"military aid|military assistance|security assistance|weapons|arms|missiles|air defen[cs]e|ammunition|tanks|fighter jets",
    "ECONOMIC_AID": r"aid|loan|loans|grant|financial support|funding|humanitarian assistance|bailout|economic assistance",
    "SANCTIONED": r"sanction|sanctions|sanctioned|sanctioning|embargo|asset freeze|blacklist(?:ed)?",
    "EXPELLED_DIPLOMAT": r"expel|expels|expelled|expulsion|persona non grata|recall(?:ed|s)? (?:its |their )?ambassador|"
                         r"summon(?:ed|s)? (?:the |its )?ambassador|sever(?:ed|s)? (?:diplomatic )?ties",
    "CONDEMNED": r"condemn\w*|critici[sz]\w*|denounc\w*|accus\w*|rebuk\w*|slammed|warned|threaten\w*|blamed",
    "SUPPORTED": r"support\w*|backed|backing|welcomed|praised|endorsed|congratulated|thanked|pledged",
}

TRIGGERS = {
    "vi": {r: re.compile(rf"(?<!\w)(?:{p})(?!\w)", re.IGNORECASE) for r, p in _VI.items()},
    "en": {r: re.compile(rf"\b(?:{p})\b", re.IGNORECASE) for r, p in _EN.items()},
}


def find_triggers(sentence: str, lang: str) -> list[str]:
    """Relation types whose trigger words occur in the sentence."""
    return [rel for rel, rx in TRIGGERS[lang].items() if rx.search(sentence)]
