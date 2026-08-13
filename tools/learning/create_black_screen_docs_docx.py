from __future__ import annotations

from html import escape
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile


PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = PROJECT_ROOT / "docs"
OUT_PATH = OUT_DIR / "Media_Monitor_Architecture_Guide.docx"
LEGACY_OUT_PATH = OUT_DIR / "Black_Screen_Pipeline_Guide.docx"
LEGACY_FALLBACK_OUT_PATH = (
    OUT_DIR / "Black_Screen_Pipeline_Guide_updated.docx"
)


CONTENT = [
    ("title", "Tài liệu kiến trúc Media Monitor"),
    (
        "p",
        (
            "Tài liệu này giải thích pipeline mới của project media-monitor sau "
            "khi refactor sang kiến trúc check-based. Mục tiêu là giúp bạn hiểu "
            "luồng chạy chính, ý tưởng của từng file, và những hàm quan trọng cần "
            "nắm khi debug hoặc thêm check mới. Tài liệu không giải thích từng dòng "
            "code, mà tập trung vào bức tranh lớn."
        ),
    ),
    ("h1", "1. Vấn đề của kiến trúc cũ"),
    (
        "p",
        (
            "Trước đây main.py nhận --case rồi gọi trực tiếp runner tương ứng, ví dụ "
            "black_screen hoặc freeze_frame. Mỗi runner lại tự parse master playlist "
            "và media playlist riêng. Cách này chạy được khi chỉ test một case, nhưng "
            "không phù hợp production, vì production chỉ có một master.m3u8 và không "
            "biết trước video bị lỗi gì."
        ),
    ),
    (
        "p",
        (
            "Một video có thể vừa bị black screen, vừa bị freeze frame, và sau này có "
            "thể có thêm color bars, macroblocking, no signal, v.v. Vì vậy hệ thống "
            "cần chạy nhiều check trên cùng một context thay vì bắt người dùng chọn "
            "sẵn bệnh bằng --case."
        ),
    ),
    ("h1", "2. Kiến trúc mới"),
    (
        "code",
        (
            "master.m3u8\n"
            "  -> MonitoringContext\n"
            "  -> MediaMonitorService\n"
            "  -> BlackScreenCheck\n"
            "  -> FreezeFrameCheck\n"
            "  -> các check tương lai\n"
            "  -> MonitorReport\n"
            "  -> reporting.console"
        ),
    ),
    (
        "p",
        (
            "Ý tưởng chính: parse HLS một lần ở tầng core, sau đó tất cả check dùng "
            "chung dữ liệu này. Check nào cần segment thì lấy từ context, không tự "
            "load lại master/media playlist nữa."
        ),
    ),
    ("h1", "3. MonitoringContext"),
    (
        "p",
        (
            "File: src/core/context.py. Đây là nơi build dữ liệu dùng chung cho một "
            "lần monitor. MonitoringContext hiện có master_url, danh sách variants, "
            "và segments_by_variant."
        ),
    ),
    (
        "code",
        (
            "MonitoringContext(\n"
            "    master_url=<url>,\n"
            "    variants=[Variant(...), ...],\n"
            "    segments_by_variant={\"720p\": [Segment(...)]}\n"
            ")"
        ),
    ),
    (
        "p",
        (
            "Hàm quan trọng là build_monitoring_context(master_url). Hàm này gọi "
            "parse_master_playlist một lần, sau đó gọi parse_media_playlist cho từng "
            "variant một lần. Các check phía sau chỉ dùng context đã build."
        ),
    ),
    (
        "p",
        (
            "Nếu sau này cần thêm thông tin dùng chung như metadata, codec, audio stream "
            "info, duration hoặc probe result, nên thêm vào context hoặc một object con "
            "của context. Không nên để từng check tự probe lại nếu dữ liệu đó có thể dùng chung."
        ),
    ),
    ("h1", "4. MediaCheck và CheckResult"),
    (
        "p",
        (
            "File: src/core/monitor.py. MediaCheck là protocol/interface chung cho tất "
            "cả check. Mỗi check chỉ cần có name và hàm run(context)."
        ),
    ),
    (
        "code",
        (
            "class MediaCheck(Protocol):\n"
            "    name: str\n"
            "    def run(self, context: MonitoringContext) -> CheckResult:\n"
            "        ..."
        ),
    ),
    (
        "p",
        (
            "CheckResult là kết quả chuẩn của một check. Nó có name, result và has_issue. "
            "result có thể là bất kỳ object nào của check đó, vì black screen và freeze "
            "frame có cấu trúc kết quả khác nhau."
        ),
    ),
    ("h1", "5. MediaMonitorService"),
    (
        "p",
        (
            "File: src/core/monitor.py. MediaMonitorService nhận danh sách checks, build "
            "MonitoringContext, chạy từng check, rồi gom kết quả thành MonitorReport."
        ),
    ),
    (
        "code",
        (
            "service = MediaMonitorService(default_checks())\n"
            "report = service.run(master_url)\n"
            "\n"
            "# debug filter\n"
            "report = service.run(master_url, only=[\"black_screen\"])"
        ),
    ),
    (
        "p",
        (
            "Điểm quan trọng: service build context một lần cho một master_url. Nếu chạy "
            "cả black_screen và freeze_frame thì hai check dùng chung variants và "
            "segments_by_variant trong context."
        ),
    ),
    ("h1", "6. Registry"),
    (
        "p",
        (
            "File: src/core/registry.py. Registry hiện có default_checks(), trả về "
            "BlackScreenCheck, FreezeFrameCheck và AudioLossCheck. Khi thêm check mới, ví dụ "
            "MacroblockingCheck, bạn chỉ cần tạo class check mới và register vào "
            "default_checks."
        ),
    ),
    (
        "code",
        (
            "def default_checks():\n"
            "    return [\n"
            "        BlackScreenCheck(),\n"
            "        FreezeFrameCheck(),\n"
            "        AudioLossCheck(),\n"
            "        # MacroblockingCheck(),\n"
            "    ]"
        ),
    ),
    ("h1", "7. BlackScreenCheck"),
    (
        "p",
        (
            "File: src/checks/black_screen/check.py. Check này wrap logic black screen "
            "hiện có. Thuật toán, threshold, FFmpeg command, score và classifier được "
            "giữ nguyên. Điểm khác là check không tự parse playlist nữa mà lấy variants "
            "và segments từ MonitoringContext."
        ),
    ),
    (
        "code",
        (
            "for variant in context.variants:\n"
            "    segments = context.segments_for_variant(variant)\n"
            "    detection_results = detector.detect(segment)\n"
            "    events = aggregator.aggregate(detection_results)\n"
            "    evidence = bitstream/audio/transition\n"
            "    classification = classifier.classify(...)"
        ),
    ),
    (
        "p",
        (
            "Output raw của check là dict theo variant_id. Mỗi value là "
            "BlackScreenVariantResult gồm variant, detection_results, events và "
            "event_analyses."
        ),
    ),
    (
        "p",
        (
            "Các helper cần lưu ý trong file này: _segments_for_event lấy đúng segment "
            "bị black event ảnh hưởng; _event_overlap_ratio tính overlap giữa event "
            "các variant; _cross_variant_evidence tạo evidence cross-variant; "
            "_classify_events classify sau khi đã có đủ local evidence và cross-variant evidence."
        ),
    ),
    ("h1", "8. Các thành phần bên trong black_screen"),
    (
        "p",
        (
            "src/detectors/black_screen.py: BlackScreenDetector chạy FFmpeg blackdetect "
            "trên từng segment. Nó chỉ phát hiện khoảng đen, không kết luận đó là lỗi "
            "hay cảnh dựng có ý đồ."
        ),
    ),
    (
        "p",
        (
            "src/events/black_event_aggregator.py: BlackEventAggregator gom các "
            "BlackInterval theo segment thành BlackScreenEvent trên timeline của variant."
        ),
    ),
    (
        "p",
        (
            "src/analyzers/bitstream.py: BitstreamAnalyzer decode các segment bị ảnh "
            "hưởng để tìm issue như invalid NAL, missing reference, corrupt packet, "
            "timestamp error. CHECK_FAILED không bị coi là BITSTREAM_ERROR."
        ),
    ),
    (
        "p",
        (
            "src/analyzers/audio.py: AudioAnalyzer dùng ffprobe để xác định có audio hay "
            "không, sau đó dùng silencedetect trong khoảng black event để tính "
            "silence_ratio và audio_active_during_black."
        ),
    ),
    (
        "p",
        (
            "src/analyzers/transition.py: TransitionAnalyzer đo luminance YAVG quanh "
            "black_start và black_end để phân biệt fade với abrupt boundary jump."
        ),
    ),
    (
        "p",
        (
            "src/classifiers/black_screen.py: BlackScreenClassifier tính technical_score "
            "từ duration, bitstream, audio, transition và cross-variant. confidence được "
            "tính từ độ lệch của technical_score khỏi vùng 0.5."
        ),
    ),
    ("h1", "9. FreezeFrameCheck"),
    (
        "p",
        (
            "File: src/checks/freeze_frame/check.py. Check này wrap logic freeze frame "
            "hiện có. Nó dùng context để lấy variants và segments, sau đó gọi "
            "detect_freeze trên media playlist của từng variant."
        ),
    ),
    (
        "p",
        (
            "FreezeFrameCheck vẫn tính total_duration từ segments, map interval về "
            "FreezeEvent bằng _map_interval_to_event, và trả về FreezeFrameVariantResult "
            "theo variant_id. Thuật toán detect freeze không bị đổi trong refactor này."
        ),
    ),
    ("h1", "10. AudioLossCheck"),
    (
        "p",
        (
            "File: src/checks/audio_loss/check.py. Check này chỉ điều phối bốn component: "
            "AudioStreamAnalyzer, AudioSilenceDetector, AudioEventAggregator và "
            "AudioLossClassifier. Nó không trực tiếp parse ffprobe/ffmpeg output."
        ),
    ),
    (
        "p",
        (
            "src/analyzers/audio_stream.py kiểm tra audio stream, decode và timestamp. "
            "src/detectors/audio_silence.py chỉ đo raw silence interval bằng silencedetect. "
            "src/events/audio_event_aggregator.py đổi local segment time sang global timeline "
            "và merge qua HLS boundary. src/classifiers/audio_loss.py mới áp business rule "
            "như continuous silence hoặc intermittent audio loss."
        ),
    ),
    ("h1", "11. Reporting và main.py"),
    (
        "p",
        (
            "File: src/reporting/console.py chứa toàn bộ logic print ra terminal. main.py "
            "không còn chứa hàm print chi tiết black/freeze nữa."
        ),
    ),
    (
        "p",
        (
            "File: src/main.py giờ chỉ là CLI entry point mỏng: parse --url, parse --only "
            "hoặc --case deprecated alias, tạo MediaMonitorService, chạy service, rồi gọi "
            "print_monitor_report(report)."
        ),
    ),
    (
        "code",
        (
            "# production\n"
            "python src/main.py --url <master.m3u8>\n"
            "\n"
            "# debug một check\n"
            "python src/main.py --url <master.m3u8> --only black_screen"
        ),
    ),
    ("h1", "12. MonitorReport"),
    (
        "p",
        (
            "MonitorReport nằm trong src/core/monitor.py. Report gồm master_url, context "
            "và results. results là dict theo check name, ví dụ results['black_screen'] "
            "và results['freeze_frame']."
        ),
    ),
    (
        "p",
        (
            "Một report có thể chứa nhiều loại issue cùng lúc. Ví dụ black_screen có "
            "has_issue=True và freeze_frame cũng has_issue=True. Đây là điểm khác với "
            "kiến trúc --case cũ, vì production không cần biết trước video bị lỗi gì."
        ),
    ),
    ("h1", "13. Cách thêm một check mới"),
    (
        "code",
        (
            "1. Tạo folder: src/checks/<ten_check>/check.py\n"
            "2. Tạo class <TenCheck> với name và run(context)\n"
            "3. Dùng context.variants và context.segments_for_variant(...)\n"
            "4. Trả về CheckResult(name, result, has_issue)\n"
            "5. Register vào src/core/registry.py default_checks()"
        ),
    ),
    (
        "p",
        (
            "Nguyên tắc quan trọng: nếu dữ liệu có thể dùng chung, hãy đưa vào "
            "MonitoringContext hoặc một shared analyzer. Không nên để check mới tự "
            "parse/download/probe lại nếu core đã có dữ liệu."
        ),
    ),
    ("h1", "14. Các test liên quan"),
    (
        "p",
        (
            "Tests của black_screen và freeze_frame hiện gọi trực tiếp check API mới "
            "thông qua build_monitoring_context(...) và Check.run_raw(...). Tests ở "
            "tests/core/test_media_monitor_service.py kiểm tra service chạy nhiều checks, "
            "--only filter, context build một lần, và report có thể chứa nhiều issue type."
        ),
    ),
    ("h1", "15. Tóm tắt nhanh"),
    (
        "p",
        (
            "Nếu chỉ cần nhớ một câu: main.py không quyết định video bị bệnh gì nữa. "
            "main.py đưa master.m3u8 vào MediaMonitorService, service build context "
            "một lần, sau đó các check đã register tự chạy và trả kết quả về MonitorReport."
        ),
    ),
]


def paragraph_xml(kind: str, text: str) -> str:
    style = {
        "title": "Title",
        "h1": "Heading1",
        "code": "IntenseQuote",
    }.get(kind)

    runs = []
    for index, line in enumerate(text.split("\n")):
        if index:
            runs.append("<w:r><w:br/></w:r>")
        runs.append(
            "<w:r><w:t xml:space=\"preserve\">"
            f"{escape(line)}"
            "</w:t></w:r>"
        )

    style_xml = ""
    if style:
        style_xml = (
            "<w:pPr>"
            f"<w:pStyle w:val=\"{style}\"/>"
            "</w:pPr>"
        )

    return f"<w:p>{style_xml}{''.join(runs)}</w:p>"


def build_document_xml() -> str:
    body = "\n".join(
        paragraph_xml(kind, text)
        for kind, text in CONTENT
    )

    return f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    {body}
    <w:sectPr>
      <w:pgSz w:w="11906" w:h="16838"/>
      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440"/>
    </w:sectPr>
  </w:body>
</w:document>
"""


CONTENT_TYPES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
  <Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>
</Types>
"""

RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
"""

DOCUMENT_RELS_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>
"""

STYLES_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:style w:type="paragraph" w:default="1" w:styleId="Normal">
    <w:name w:val="Normal"/>
    <w:rPr><w:sz w:val="22"/><w:szCs w:val="22"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Title">
    <w:name w:val="Title"/>
    <w:rPr><w:b/><w:sz w:val="36"/><w:szCs w:val="36"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="Heading1">
    <w:name w:val="heading 1"/>
    <w:basedOn w:val="Normal"/>
    <w:next w:val="Normal"/>
    <w:qFormat/>
    <w:rPr><w:b/><w:sz w:val="28"/><w:szCs w:val="28"/></w:rPr>
  </w:style>
  <w:style w:type="paragraph" w:styleId="IntenseQuote">
    <w:name w:val="Intense Quote"/>
    <w:basedOn w:val="Normal"/>
    <w:pPr><w:ind w:left="360"/><w:spacing w:before="120" w:after="120"/></w:pPr>
    <w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/><w:sz w:val="20"/><w:szCs w:val="20"/></w:rPr>
  </w:style>
</w:styles>
"""


def write_docx(path: Path) -> None:
    with ZipFile(path, "w", ZIP_DEFLATED) as docx:
        docx.writestr("[Content_Types].xml", CONTENT_TYPES_XML)
        docx.writestr("_rels/.rels", RELS_XML)
        docx.writestr("word/_rels/document.xml.rels", DOCUMENT_RELS_XML)
        docx.writestr("word/styles.xml", STYLES_XML)
        docx.writestr("word/document.xml", build_document_xml())


def main() -> None:
    OUT_DIR.mkdir(exist_ok=True)
    write_docx(OUT_PATH)
    print(OUT_PATH)

    try:
        write_docx(LEGACY_OUT_PATH)
        print(LEGACY_OUT_PATH)
    except PermissionError:
        write_docx(LEGACY_FALLBACK_OUT_PATH)
        print(LEGACY_FALLBACK_OUT_PATH)


if __name__ == "__main__":
    main()
