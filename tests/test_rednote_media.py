import json

from msgspec import convert
import pytest
from render_test_support import ROOT

from nonebot_plugin_parser_lite.data import LivePhotoContent, VideoContent
from nonebot_plugin_parser_lite.parsers.rednote import discovery
from nonebot_plugin_parser_lite.parsers.rednote.discovery import (
    Image,
    Stream,
    StreamUrl,
    decoder,
)


@pytest.fixture
def note():
    return decoder.decode(
        (ROOT / "api_txt/rednote/video.json").read_bytes()
    ).noteData.data.noteData


@pytest.mark.asyncio
@pytest.mark.parametrize("has_stream", [True, False])
async def test_main_video_passes_stream_backups_and_retry_policy(
    monkeypatch, note, has_stream
):
    video = note.video
    expected_urls = video.media.stream.urls if has_stream else [video.url]
    if not has_stream:
        video.media = None
    calls = []

    async def streamd(**kwargs):
        calls.append(kwargs)
        return "downloaded.mp4"

    monkeypatch.setattr(discovery.DOWNLOADER, "streamd", streamd)
    content = note.medias[0]
    assert isinstance(content, VideoContent)
    assert calls == []  # 构建消息时不发起下载
    assert await content.path_task == "downloaded.mp4"
    assert calls[0]["url"] == expected_urls[0]
    assert calls[0]["fallback_urls"] == expected_urls[1:]
    assert calls[0]["retry_http_statuses"] == frozenset({422, 424})
    assert calls[0]["cache_key"] == f"video:rednote:{video.consumer.originVideoKey}"
    assert content.duration == video.capa.duration


@pytest.mark.asyncio
async def test_live_photo_passes_backups_and_preserves_motion_cache(monkeypatch, note):
    note.video = None
    note.imageList = [
        Image(
            fileId="live-photo",
            livePhoto=True,
            stream=Stream(
                h264=[
                    StreamUrl(
                        "https://primary.example/live.mp4",
                        ["https://backup.example/live.mp4"],
                    )
                ]
            ),
        )
    ]
    calls = []

    async def streamd(**kwargs):
        calls.append(kwargs)
        return "motion.mp4"

    monkeypatch.setattr(discovery.DOWNLOADER, "streamd", streamd)
    content = note.medias[0]
    assert isinstance(content, LivePhotoContent)
    assert await content.path_task == "motion.mp4"
    assert calls[0]["fallback_urls"] == ["https://backup.example/live.mp4"]
    assert calls[0]["retry_http_statuses"] == frozenset({422, 424})
    assert calls[0]["cache_key"] == "video:rednote:live-photo:motion"


def test_stream_filters_empty_urls_and_keeps_backups_of_one_representation():
    stream = convert(
        {
            "h264": [
                {"masterUrl": ""},
                {
                    "masterUrl": "primary",
                    "backupUrls": ["", "primary", "backup", "backup"],
                },
                {"masterUrl": "different-resolution"},
            ],
            "h265": [{"masterUrl": "different-codec"}],
        },
        Stream,
    )
    assert stream.urls == ["primary", "backup"]
    assert stream.url == "primary"


def test_stream_uses_backup_when_master_is_empty():
    stream = Stream(h265=[StreamUrl("", ["backup"])])
    assert stream.urls == ["backup"]


def test_legacy_video_sample_decodes_without_media():
    data = json.loads((ROOT / "api_txt/rednote/video.json").read_text())
    del data["noteData"]["data"]["noteData"]["video"]["media"]
    video = decoder.decode(json.dumps(data)).noteData.data.noteData.video
    assert video.urls == [video.url]


def test_empty_stream_has_no_urls():
    assert Stream().urls == []
    with pytest.raises(ValueError, match="no available stream"):
        _ = Stream().url
