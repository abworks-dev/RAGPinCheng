"""Deterministic, versioned terminology correction for transcript text."""
from __future__ import annotations

import re

from .types import TerminologyCorrectionConfig

BIM_ENGINEERING_TERMS_V1 = (
    "Revit",
    "Navisworks",
    "AutoCAD",
    "BIM",
    "BIM-2026-0805",
    "12.5",
    "208",
    "95%",
)


_RULES_V1 = (
    (re.compile(r"(?<![A-Za-z0-9])auto[ \t]+cad(?![A-Za-z0-9])", re.IGNORECASE), "AutoCAD"),
    (re.compile(r"(?<![A-Za-z0-9])b[ \t]+i[ \t]+m(?![A-Za-z0-9])", re.IGNORECASE), "BIM"),
    (re.compile(r"(?<![A-Za-z0-9])revit(?![A-Za-z0-9])", re.IGNORECASE), "Revit"),
    (re.compile(r"(?<![A-Za-z0-9])navisworks(?![A-Za-z0-9])", re.IGNORECASE), "Navisworks"),
    # 中文同音/形近误识纠正。仅收真实生产稿中反复出现的形态，且都是"正确写法唯一"的
    # 情形（结构/楼梯/梯段/梯柱/剖面/三维/检查/图纸…），不猜测语义、不做同义词替换。
    (re.compile(r"结枅|结枯|结枸|结枡"), "结构"),
    (re.compile(r"楼坯|楼姟|搂梯"), "楼梯"),
    (re.compile(r"楼成(?=平台|面|结构)"), "楼层"),
    # 先纠正"误识的热词本身"，否则下面的通用规则会把「建筒碰撞」改成「建筑碰撞」，
    # 使回吐纯度判定误认为存在真实内容而整段保留。
    (re.compile(r"建筒碰撞|建筥碰撞|建筒构碰"), "构件碰撞"),
    (re.compile(r"建筒模型|建筥模型"), "建筑信息模型"),
    (re.compile(r"建筒|建筥|建筡"), "建筑"),
    (re.compile(r"梨柱|梭柱|梨组|梯组"), "梯柱"),
    (re.compile(r"提梁踢柱|踢梁踢柱"), "梯梁梯柱"),
    (re.compile(r"踢断|梯断|梭段"), "梯段"),
    (re.compile(r"踢面|题面"), "踏面"),
    (re.compile(r"平态|平毯|平臺"), "平台"),
    (re.compile(r"结购|结枡"), "结构"),
    (re.compile(r"臺"), "台"),
    (re.compile(r"梯株"), "梯柱"),
    (re.compile(r"梯量|梯亮|梯岚|提梁"), "梯梁"),
    (re.compile(r"提柱"), "梯柱"),
    (re.compile(r"建筚"), "建筑"),
    (re.compile(r"检杳"), "检查"),
    (re.compile(r"减高|进高"), "净高"),
    # 去热词解码实测（2026-09-13 自然长音频样本 + 真实长视频）：这些是模型在无热词时
    # 反复写错的形态，且都只有唯一正确写法。故意不收"挺高"——"这个梯梁挺高的"是合法口语，
    # 全局替换会改坏正常句子。
    (re.compile(r"静高|径高|净够"), "净高"),
    (re.compile(r"构建碰撞"), "构件碰撞"),
    (re.compile(r"碰壮"), "碰撞"),
    (re.compile(r"结构构建"), "结构构件"),
    (re.compile(r"标柱"), "标注"),
    (re.compile(r"铺面(?=视图|图)"), "剖面"),
    (re.compile(r"屏面"), "平面"),
    (re.compile(r"数面"), "踏面"),
    (re.compile(r"确确"), "确实"),
    (re.compile(r"剥面|头面|刮面"), "剖面"),
    (re.compile(r"头切"), "剖切"),
    (re.compile(r"三围"), "三维"),
    (re.compile(r"检察"), "检查"),
    (re.compile(r"图质"), "图纸"),
    (re.compile(r"净靠"), "净高"),
    (re.compile(r"标靠|比辅高"), "标高"),
    (re.compile(r"核兑"), "核对"),
    (re.compile(r"连泡化|连泊化"), "连梁化"),
    # 繁体字形（模型偶发输出），统一为简体，避免同一术语在稿中出现两种写法。
    (re.compile(r"圖"), "图"),
    (re.compile(r"紙"), "纸"),
    (re.compile(r"標"), "标"),
    (re.compile(r"對"), "对"),
    (re.compile(r"結"), "结"),
    (re.compile(r"構"), "构"),
    (re.compile(r"樓"), "楼"),
    (re.compile(r"檢"), "检"),
    (re.compile(r"測"), "测"),
    (re.compile(r"規"), "规"),
    (re.compile(r"範"), "范"),
    (re.compile(r"確"), "确"),
    (re.compile(r"認"), "认"),
    (re.compile(r"體"), "体"),
)
_BIM_CODE = re.compile(
    r"(?<![A-Za-z0-9])BIM[ \t]*[- ][ \t]*(\d{4})[ \t]*[- ][ \t]*(\d{4})(?!\d)",
    re.IGNORECASE,
)
_STANDARD_CODE = re.compile(
    r"(?<![A-Za-z0-9])(?P<prefix>GB(?:/T)?|JGJ|DG/TJ)[ \t]*(?P<number>\d{3,6})[ \t]*(?:-| )[ \t]*(?P<year>\d{4})(?!\d)",
    re.IGNORECASE,
)
_DECIMAL = re.compile(r"(?<!\d)(\d+)[ \t]*\.[ \t]*(\d+)(?!\d)")
_PERCENT = re.compile(r"(?<!\d)(\d+(?:\.\d+)?)[ \t]+%(?![A-Za-z0-9])")
_PROTECTED = re.compile(
    r"(?<![A-Za-z0-9])(?:AutoCAD|Navisworks|Revit|BIM(?:-\d{4}-\d{4})?|GB(?:/T)? \d{3,6}-\d{4}|JGJ \d{3,6}-\d{4}|DG/TJ \d{3,6}-\d{4}|12\.5|208|95%)(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def correct_terminology(
    text: str, config: TerminologyCorrectionConfig | None
) -> tuple[str, bool]:
    if config is None or config.rule_set_id == "none":
        return text, False
    corrected = text
    for pattern, replacement in _RULES_V1:
        corrected = pattern.sub(replacement, corrected)
    corrected = _BIM_CODE.sub(lambda match: f"BIM-{match.group(1)}-{match.group(2)}", corrected)
    corrected = _STANDARD_CODE.sub(
        lambda match: f"{match.group('prefix').upper()} {match.group('number')}-{match.group('year')}",
        corrected,
    )
    corrected = _DECIMAL.sub(lambda match: f"{match.group(1)}.{match.group(2)}", corrected)
    corrected = _PERCENT.sub(lambda match: f"{match.group(1)}%", corrected)
    return corrected, corrected != text


def protected_terminology_spans(
    text: str, config: TerminologyCorrectionConfig | None
) -> tuple[tuple[int, int], ...]:
    if config is None or config.rule_set_id == "none":
        return ()
    return tuple((match.start(), match.end()) for match in _PROTECTED.finditer(text))


# ---------------------------------------------------------------------------
# Conservative hallucinated standard/term tail cleanup.
#
# Real long-media whisperx/faster decode, prompted by high-bias hotwords, can
# append "hallucinated" filler at low-information pauses/segment ends, e.g.
#   "建筑抗震设复核", "建筑抗震设复核 钢结构",
#   "建筑抗震设复核 规范 GB 50011-2010", "净高分析规范 GB 50011-2010 建筑抗震设复核",
#   "建筑抗震设复核 净高分析规范 GB 50011-2014"   <- GB 50011-2014 does not exist
# where the speaker never actually said those terms/numbers. This cleanup is
# deliberately CONSERVATIVE: it DROPS a whole segment only when its text is
# composed exclusively of the recognised bias words/phrases, standard-code
# tokens, and generic 规范/编号 connectors — i.e. no other meaningful content.
# Any segment containing real speech (measurements, verbs, other Han) is left
# untouched, so genuine citations are preserved. Fabricated code *years* are
# handled implicitly: a fake edition like GB 50011-2014 only ever survives
# inside a bias-only line, which is dropped here; we never rewrite an embedded
# year inside real speech. No engine, no qualification gate, no GPU threshold
# is changed.
# ---------------------------------------------------------------------------
# "Noise words" that appear only as hallucinated filler when isolated.
_BIAS_WORDS = (
    "建筑抗震设复核",
    "建筑设计防火规范",
    "建筑抗震设计规范",
    "净高分析规范",
    "净高分析",
    "构件碰撞",
    "建筑信息模型",
    "规范编号",
    "复核",
    "钢结构",
    "焊缝",
    "螺栓",
)
# Generic connectors/code tokens that, inside an otherwise pure-bias line, are
# still hallucinated filler (e.g. "... 规范 GB 50011-2010"). A real speech
# segment always leaves other words, so removing these only triggers the drop
# when nothing meaningful remains.
_BIAS_CONNECTORS = ("规范", "编号")
_BIAS_STANDARD_CODE = re.compile(
    r"(?:GB(?:/T)?|JGJ|DG/TJ)\s*\d{3,6}\s*(?:-| )\s*\d{0,4}|"
    r"(?:GB(?:/T)?|JGJ|DG/TJ)\s*\d{3,6}",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Misrecognised spellings of the bias phrases above. Production decode writes these
# variants (e.g. 「建筒碰撞」 for 「构件碰撞」), which used to defeat the purity test
# because the variant was treated as real content.
_BIAS_VARIANTS = (
    (re.compile(r"建筒碰撞|建筥碰撞|建筒构碰"), "构件碰撞"),
    (re.compile(r"建筒模型|建筥模型"), "建筑信息模型"),
    (re.compile(r"净高分证明|净高分识|净高分析规范"), "净高分析"),
    (re.compile(r"建筒|建筥|建筡"), "建筑"),
    (re.compile(r"钢结枸|钢结枯"), "钢结构"),
    (re.compile(r"焊健|焊缝隙"), "焊缝"),
    (re.compile(r"螺检|螺拴"), "螺栓"),
)


def _normalize_bias_variants(text: str) -> str:
    for pattern, replacement in _BIAS_VARIANTS:
        text = pattern.sub(replacement, text)
    return text


# ---------------------------------------------------------------------------
# Whisper 在音乐/静音段会吐出训练语料里的字幕组署名与片尾套话（实测：某段静音后
# 附加「中文字幕志愿者 杨茜茜」），与本项目内容无关。这些短语不可能由讲师口述，
# 因此按"整行署名"清理；只在独立行上生效（合并段用换行连接），避免误删正文同形词。
_WHISPER_BOILERPLATE = (
    "中文字幕志愿者",
    "字幕志愿者",
    "中文字幕",
    "字幕组",
    "字幕由",
    "本字幕",
    "翻译：",
    "翻译:",
    "校对：",
    "校对:",
    "时间轴：",
    "时间轴:",
    "听译：",
    "听译:",
    "压制：",
    "压制:",
    "感谢观看",
    "感谢您的观看",
    "请不吝点赞",
    "订阅频道",
    "明镜与点点",
    "MING PAO",
    "点点栏目",
)
# 署名后通常跟一个短名字（如「杨茜茜」）；去掉署名短语后只剩短名字即视为署名行。
_BOILERPLATE_REMAINDER = re.compile(r"^[\u4e00-\u9fffA-Za-z·.\s]{0,8}$")


def _line_is_boilerplate(line: str) -> bool:
    candidate = line.strip()
    if not candidate:
        return False
    hit = False
    for phrase in sorted(_WHISPER_BOILERPLATE, key=len, reverse=True):
        if phrase in candidate:
            hit = True
            candidate = candidate.replace(phrase, " ")
    if not hit:
        return False
    candidate = (
        candidate.replace("，", " ")
        .replace(",", " ")
        .replace("：", " ")
        .replace(":", " ")
    )
    return bool(_BOILERPLATE_REMAINDER.match(candidate))


def strip_whisper_boilerplate(text: str) -> tuple[str, bool]:
    """Remove whole lines that are only a subtitle credit or an outro cliché."""
    if "\n" not in text:
        return ("", True) if _line_is_boilerplate(text) else (text, False)
    kept = [line for line in text.split("\n") if not _line_is_boilerplate(line)]
    cleaned = "\n".join(kept).strip("\n")
    if not cleaned.strip():
        return "", True
    return cleaned, cleaned != text


# 解码在重复/不确定语音处会整串复读同一个短语（实测「楼梯楼梯楼梯…」与热词串
# 反复）。引擎不再用 3-gram 禁令换取覆盖率，这里做保守折叠：同一个 2-4 字片段
# 连续出现 4 次以上、或同一个 6-24 字片段连续出现 3 次以上，只保留一次。
# 正常口语的叠词（看看、慢慢）最多两次，不会被命中。
_REPEAT_SHORT = re.compile(r"([\u4e00-\u9fffA-Za-z0-9]{2,4})\1{3,}")
# 短语复读要能覆盖带连字符/点号的规范编号（实测「建筑抗震设计规范 GB 40011-2014」
# 连续 9 次），因此字符类必须含 - . / % 这些编号里出现的符号。
_REPEAT_PHRASE = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9][\u4e00-\u9fffA-Za-z0-9 \-./%]{5,38})\1{2,}"
)
# 长短语只重复两次同样是复读（实测「…GB 40011-2014」成对出现）；10 字以上的短语
# 逐字重复两次不会是正常口述。
_REPEAT_LONG_PAIR = re.compile(
    r"([\u4e00-\u9fffA-Za-z0-9][\u4e00-\u9fffA-Za-z0-9 \-./%]{9,38})\1"
)


def collapse_degenerate_repeats(text: str) -> tuple[str, bool]:
    """Collapse decode loops that repeat one short phrase over and over."""
    collapsed = _REPEAT_SHORT.sub(r"\1", text)
    collapsed = _REPEAT_PHRASE.sub(r"\1", collapsed)
    # A loop can nest (phrase of a phrase), so settle with a bounded second pass.
    collapsed = _REPEAT_SHORT.sub(r"\1", collapsed)
    collapsed = _REPEAT_PHRASE.sub(r"\1", collapsed)
    collapsed = _REPEAT_LONG_PAIR.sub(r"\1", collapsed)
    return collapsed, collapsed != text


def _is_bias_only(text: str) -> bool:
    """True if text is composed only of recognised bias words/phrases, standard
    code tokens, generic 规范/编号 connectors, whitespace and commas — i.e. no
    other meaningful Han/ASCII/numeral content (real speech)."""
    candidate = _normalize_bias_variants(text)
    for phrase in sorted(_BIAS_WORDS, key=len, reverse=True):
        candidate = candidate.replace(phrase, " ")
    for connector in _BIAS_CONNECTORS:
        candidate = candidate.replace(connector, " ")
    candidate = _BIAS_STANDARD_CODE.sub(" ", candidate)
    candidate = candidate.replace("，", " ").replace(",", " ").replace("/", " ").replace("／", " ")
    candidate = re.sub(r"[\s。.、·—\-:：\u3000]", "", candidate)
    return candidate == ""


# A run of at least this many consecutive bias phrases at a segment edge is filler
# even when the same (merged) segment also carries real speech. Three is deliberate:
# two adjacent genuine citations are possible, three hallucinated ones in a row are
# not something a speaker dictates.
_BIAS_RUN_MIN_PHRASES = 3
_BIAS_SEPARATORS = " \t\n\r，,。.、·—/:：\u3000"


def _leading_bias_run(text: str) -> int:
    """Length of the leading run of bias phrases/codes/connectors, or 0."""
    position = 0
    phrases = 0
    while position < len(text):
        while position < len(text) and text[position] in _BIAS_SEPARATORS:
            position += 1
        matched = None
        for phrase in sorted(_BIAS_WORDS, key=len, reverse=True):
            if text.startswith(phrase, position):
                matched = phrase
                break
        if matched is None:
            code = _BIAS_STANDARD_CODE.match(text, position)
            if code is not None:
                position = code.end()
                continue
            connector = next(
                (item for item in _BIAS_CONNECTORS if text.startswith(item, position)),
                None,
            )
            if connector is None:
                break
            position += len(connector)
            continue
        position += len(matched)
        phrases += 1
    if phrases < _BIAS_RUN_MIN_PHRASES:
        return 0
    return position


def _trailing_bias_run(text: str) -> int:
    """Offset where a trailing run of bias phrases starts, or len(text)."""
    position = len(text)
    phrases = 0
    while position > 0:
        while position > 0 and text[position - 1] in _BIAS_SEPARATORS:
            position -= 1
        matched = None
        for phrase in sorted(_BIAS_WORDS, key=len, reverse=True):
            if text.endswith(phrase, 0, position):
                matched = phrase
                break
        if matched is None:
            connector = next(
                (item for item in _BIAS_CONNECTORS if text.endswith(item, 0, position)),
                None,
            )
            if connector is None:
                break
            position -= len(connector)
            continue
        position -= len(matched)
        phrases += 1
    if phrases < _BIAS_RUN_MIN_PHRASES:
        return len(text)
    return position


def clean_hallucinated_standard(text: str) -> tuple[str, bool]:
    """Conservative scrub of hallucinated standard/code filler.

    Returns (text, dropped):
    - a whole segment is DROPPED (``"", True``) only when it is composed
      exclusively of recognised bias filler, e.g. "建筑抗震设复核",
      "净高分析规范 GB 50011-2014";
    - a segment that also carries real speech keeps that speech, but a run of at
      least three consecutive bias phrases at either edge is removed
      (``stripped_text, False``). Merging runs before this check is why a pure
      filler segment used to survive by being joined to the next real one;
    - anything else is returned unchanged, so genuine citations inside real
      sentences are preserved.
    """
    if not text or not text.strip():
        return text, False
    stripped, repeated = collapse_degenerate_repeats(text.strip())
    if _is_bias_only(stripped):
        return "", True
    normalized = _normalize_bias_variants(stripped)
    if normalized != stripped and _is_bias_only(normalized):
        return "", True
    # Subtitle credits and outro clichés are dropped per line before the bias run
    # handling, so a merged segment keeps its real speech and loses only the credit.
    without_credits, credit_changed = strip_whisper_boilerplate(stripped)
    if credit_changed and not without_credits.strip():
        return "", True
    candidate = _normalize_bias_variants(without_credits)
    result = candidate
    start = _leading_bias_run(candidate)
    end = _trailing_bias_run(candidate)
    if start or end < len(candidate):
        trimmed = candidate[start:end].strip(_BIAS_SEPARATORS + " ")
        if trimmed and not _is_bias_only(trimmed):
            result = trimmed
    if result != text:
        return result, False
    return text, False
