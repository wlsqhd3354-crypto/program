"""글 제목/본문/이미지 로테이션 매니저.

messages/ 폴더에 .txt 파일을 넣으면 자동으로 인식.
파일 첫 줄 = 제목, 나머지 = 본문.

images/ 폴더의 이미지를 메시지마다 랜덤으로 첨부 (옵션).
"""

import os
import random
from dataclasses import dataclass

from config import MESSAGES_DIR, IMAGES_DIR
from paths import resource_path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}


@dataclass
class Post:
    title: str
    body: str
    images: list[str]


def load_messages(directory: str | None = None) -> list[Post]:
    """messages/*.txt 를 전부 로드. 첫 줄=제목, 나머지=본문."""
    directory = directory or resource_path(MESSAGES_DIR)
    if not os.path.isdir(directory):
        return []
    posts: list[Post] = []
    for fname in sorted(os.listdir(directory)):
        if not fname.lower().endswith(".txt"):
            continue
        path = os.path.join(directory, fname)
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read().strip()
        if not raw:
            continue
        lines = raw.split("\n", 1)
        title = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        posts.append(Post(title=title, body=body, images=[]))
    return posts


def list_images(directory: str | None = None) -> list[str]:
    directory = directory or resource_path(IMAGES_DIR)
    if not os.path.isdir(directory):
        return []
    out = []
    for fname in sorted(os.listdir(directory)):
        ext = os.path.splitext(fname)[1].lower()
        if ext in IMAGE_EXTS:
            out.append(os.path.join(directory, fname))
    return out


class Rotator:
    """글/이미지 로테이션. mode: 'random' | 'sequential'."""

    def __init__(
        self,
        posts: list[Post],
        images: list[str] | None = None,
        mode: str = "random",
        attach_image_count: int = 1,
    ):
        if not posts:
            raise ValueError("messages/ 폴더에 .txt 파일이 없습니다.")
        self.posts = posts
        self.images = images or []
        self.mode = mode
        self.attach_image_count = max(0, attach_image_count)
        self._idx = 0

    def next(self) -> Post:
        if self.mode == "sequential":
            p = self.posts[self._idx % len(self.posts)]
            self._idx += 1
        else:
            p = random.choice(self.posts)

        chosen_imgs: list[str] = []
        if self.attach_image_count and self.images:
            k = min(self.attach_image_count, len(self.images))
            chosen_imgs = random.sample(self.images, k)

        return Post(title=p.title, body=p.body, images=chosen_imgs)
