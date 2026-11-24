"""Ngắt mạch theo nguồn trong một lần chạy (spec §7.3, `07a` §4.3.5).

Mở sau `threshold` lỗi liên tiếp và giữ trạng thái mở tới hết đời của gateway (một lần
chạy stage); không có trạng thái nửa mở. Một lần tra thành công (kể cả không tìm thấy)
đặt lại bộ đếm.
"""


class CircuitBreaker:
    def __init__(self, threshold: int) -> None:
        if threshold < 1:
            raise ValueError("threshold phải từ 1 trở lên")
        self._threshold = threshold
        self._failures = 0

    @property
    def is_open(self) -> bool:
        return self._failures >= self._threshold

    def record_success(self) -> None:
        if not self.is_open:
            self._failures = 0

    def record_failure(self) -> None:
        self._failures += 1
