from __future__ import annotations

import re

from msgspec import Struct, field
from msgspec.json import Decoder

from ...creator import Creator
from ...data import ContentItem
from ...download import DOWNLOADER
from ...utils.format import replace_placeholder_to_sticker

REDNOTE_PATTERN = re.compile(r"\[(?P<name>[^]]+[a-zA-Z])\]")
REDNOTE_RETRYABLE_HTTP_STATUSES = frozenset({422, 424})


class StreamUrl(Struct):
    """Wrapper for stream url"""

    masterUrl: str
    """主链接"""
    backupUrls: list[str] = field(default_factory=list)
    """同一视频流的备用链接"""


class Stream(Struct):
    """Wrapper for image stream"""

    h264: list[StreamUrl] = field(default_factory=list)
    h265: list[StreamUrl] = field(default_factory=list)
    h266: list[StreamUrl] = field(default_factory=list)
    av1: list[StreamUrl] = field(default_factory=list)

    @property
    def urls(self) -> list[str]:
        """按 h264 > h265 > h266 > av1 选流，仅轮换同一流的地址以支持续传。"""
        for stream_list in (self.h264, self.h265, self.h266, self.av1):
            for stream in stream_list:
                urls = list(
                    dict.fromkeys(
                        url for url in (stream.masterUrl, *stream.backupUrls) if url
                    )
                )
                if urls:
                    return urls
        return []

    @property
    def url(self) -> str:
        if urls := self.urls:
            return urls[0]
        raise ValueError("Stream.url: no available stream url found")


class Media(Struct):
    """媒体容器"""

    stream: Stream


class Consumer(Struct):
    originVideoKey: str


class Capa(Struct):
    duration: float


class Video(Struct):
    """笔记中的主视频信息"""

    capa: Capa
    consumer: Consumer
    media: Media | None = None

    @property
    def urls(self) -> list[str]:
        if self.media and (urls := self.media.stream.urls):
            return urls
        return [self.url]

    @property
    def url(self) -> str:
        """主视频直链"""
        return f"https://sns-video-bd.xhscdn.com/{self.consumer.originVideoKey}"


class Image(Struct):
    fileId: str
    livePhoto: bool = False
    """是否为 iPhone Live Photo"""
    stream: Stream = field(default_factory=Stream)
    """iPhone Live Photo 视频流"""

    @property
    def url(self) -> str:
        """图片无水印直链"""
        return (
            f"https://ci.xiaohongshu.com/{self.fileId}?imageView2/2/w/1080/format/jpg"
        )


class CommentImage(Struct):
    originUrl: str


class User(Struct):
    """用户信息"""

    nickName: str
    avatar: str
    userId: str


class InteractInfo(Struct):
    """互动信息"""

    likedCount: str
    collectedCount: str
    commentCount: str
    shareCount: str


class Cover(Struct):
    fileId: str


class NoteDetail(Struct):
    # type: str
    # """类型，一般是normal/video"""
    title: str
    """标题"""
    desc: str
    """简介"""
    user: User
    lastUpdateTime: int
    interactInfo: InteractInfo
    cover: Cover
    imageList: list[Image] = field(default_factory=list)
    """图片列表，包括普通图片和 Live Photo"""
    video: Video | None = None
    """主视频（如果有）"""

    @property
    def nickname(self) -> str:
        """作者昵称"""
        return self.user.nickName

    @property
    def avatar_url(self) -> str:
        """作者头像地址"""
        return self.user.avatar

    @property
    def medias(self) -> list[ContentItem]:
        """
        统一构建当前笔记的媒体内容列表

        - Live Photo -> LivePhotoContent
        - 普通图片   -> ImageContent
        - 主视频     -> VideoContent (如果有视频，则第一张图是封面)
        """
        items: list[ContentItem] = []
        if self.video:
            items.append(
                Creator.video(
                    url_or_task=DOWNLOADER.download_video(
                        url=self.video.urls[0],
                        fallback_urls=self.video.urls[1:],
                        retry_http_statuses=REDNOTE_RETRYABLE_HTTP_STATUSES,
                        cache_key=f"rednote:{self.video.consumer.originVideoKey}",
                    ),
                    cover_url=f"https://ci.xiaohongshu.com/{self.cover.fileId}?imageView2/2/w/1080/format/jpg",
                    duration=self.video.capa.duration,
                    cache_key=f"rednote:{self.video.consumer.originVideoKey}",
                )
            )
        else:
            for img in self.imageList:
                if img.livePhoto:
                    items.append(
                        Creator.live_photo(
                            video_url=DOWNLOADER.download_video(
                                url=img.stream.url,
                                fallback_urls=img.stream.urls[1:],
                                retry_http_statuses=REDNOTE_RETRYABLE_HTTP_STATUSES,
                                cache_key=f"rednote:{img.fileId}"
                                if img.fileId
                                else None,
                                cache_variant="motion" if img.fileId else None,
                            ),
                            image_url=img.url,
                            cache_key=f"rednote:{img.fileId}" if img.fileId else None,
                        )
                    )
                else:
                    items.append(
                        Creator.image(
                            url=img.url,
                            cache_key=f"rednote:{img.fileId}" if img.fileId else None,
                        )
                    )

        return items

    @property
    def content(self):
        return [
            *replace_placeholder_to_sticker(self.desc, REDNOTE_PATTERN, "rednote"),
            *self.medias,
        ]


class CommentUser(Struct):
    nickname: str
    image: str
    userId: str


class Comment(Struct):
    user: CommentUser
    time: int
    likeViewCount: str
    text: str = field(name="content")
    ipLocation: str = ""
    pictures: list[CommentImage] = field(default_factory=list)
    subComments: list[Comment] = field(default_factory=list)

    @property
    def content(self) -> list[ContentItem]:
        content = replace_placeholder_to_sticker(self.text, REDNOTE_PATTERN, "rednote")
        content.extend(
            Creator.image(
                url=pic.originUrl,
            )
            for pic in self.pictures
        )
        return content


class CommentList(Struct):
    comments: list[Comment] = field(default_factory=list)


class NoteDetailWrapper(Struct):
    """Wrapper for note detail, represents the value in noteDetailMap[xhs_id]"""

    noteData: NoteDetail
    commentData: CommentList = field(default_factory=CommentList)


class Note(Struct):
    """Top-level note container with noteDetailMap"""

    data: NoteDetailWrapper


class InitialState(Struct):
    """Root structure of window.__INITIAL_STATE__"""

    noteData: Note


decoder = Decoder(InitialState)
