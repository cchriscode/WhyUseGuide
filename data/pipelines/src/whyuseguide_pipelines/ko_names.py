"""헵번식 로마자 역·노선 이름 → 한글 (국립국어원 일본어 표기법).

GTFS에 한국어 이름이 없을 때 쓰는 기계 변환이다. 결과는 name_ko_source = "machine"으로 저장해
앱이 "기계 번역"으로 표시한다(ODPT 개발자 가이드라인 5). 관용이 다른 이름은 names_ko.toml로 덮어쓴다.

표기법 요점: 장음은 적지 않는다, 촉음(っ)은 ㅅ받침, ん은 ㄴ받침, つ는 '쓰',
か·た행과 ちゃ류는 첫머리에서 예사소리(가, 다, 자) 중간·끝에서 거센소리(카, 타, 차).

표시 이름은 표기법을 따르고, 여행 글에서 흔한 관용 표기(つ = '츠', 첫머리 거센소리)는
검색 별칭으로 둔다(korean_variants).
"""

import re
from collections.abc import Callable

# 로마자 → (첫머리, 중간·끝)
_SYLLABLES: dict[str, tuple[str, str]] = {}


def _add(table: str) -> None:
    for item in table.split():
        romaji, kana = item.split("=")
        first, _, rest = kana.partition("/")
        _SYLLABLES[romaji] = (first, rest or first)


_add("a=아 i=이 u=우 e=에 o=오")
_add("ka=가/카 ki=기/키 ku=구/쿠 ke=게/케 ko=고/코 ga=가 gi=기 gu=구 ge=게 go=고")
_add("sa=사 shi=시 su=스 se=세 so=소 za=자 ji=지 zu=즈 ze=제 zo=조")
_add("ta=다/타 chi=지/치 tsu=쓰 te=데/테 to=도/토 da=다 de=데 do=도")
_add("na=나 ni=니 nu=누 ne=네 no=노 ha=하 hi=히 fu=후 he=헤 ho=호")
_add("ba=바 bi=비 bu=부 be=베 bo=보 pa=파 pi=피 pu=푸 pe=페 po=포")
_add("ma=마 mi=미 mu=무 me=메 mo=모 ya=야 yu=유 yo=요 ra=라 ri=리 ru=루 re=레 ro=로 wa=와 wo=오")
_add("kya=갸/캬 kyu=규/큐 kyo=교/쿄 gya=갸 gyu=규 gyo=교 sha=샤 shu=슈 sho=쇼 ja=자 ju=주 jo=조")
_add("cha=자/차 chu=주/추 cho=조/초 nya=냐 nyu=뉴 nyo=뇨 hya=햐 hyu=휴 hyo=효")
_add("bya=뱌 byu=뷰 byo=뵤 pya=퍄 pyu=퓨 pyo=표 mya=먀 myu=뮤 myo=묘 rya=랴 ryu=류 ryo=료")

_BY_LENGTH = sorted(_SYLLABLES, key=len, reverse=True)
_NIEUN, _SIOT = 4, 19  # 한글 받침 인덱스: ㄴ, ㅅ
_VOWELS = "aiueo"


def _add_final(syllables: list[str], final: int) -> None:
    if not syllables:
        raise ValueError("final consonant without a preceding syllable")
    code = ord(syllables[-1]) - 0xAC00
    if not 0 <= code < 11172 or code % 28:
        raise ValueError(f"cannot add final consonant to {syllables[-1]!r}")
    syllables[-1] = chr(0xAC00 + code + final)


def _segment(seg: str, syllables: list[str], aspirate_initial: bool, tsu: str) -> None:
    i = 0
    while i < len(seg):
        rest = seg[i:]
        nxt = rest[1:2]
        # ん: n 다음이 모음·y가 아니거나 끝, 또는 b·m·p 앞의 m (헵번식 Nihombashi, Kishibojimmae)
        if (rest[0] == "n" and (not nxt or (nxt not in _VOWELS and nxt != "y"))) or (
            rest[0] == "m" and nxt in ("b", "m", "p")
        ):
            _add_final(syllables, _NIEUN)
            i += 1
            continue
        # っ: 같은 자음 두 번(kk, ss, tt, pp) 또는 tch
        if rest.startswith("tch") or (nxt and rest[0] == nxt and rest[0] not in _VOWELS + "n"):
            _add_final(syllables, _SIOT)
            i += 1
            continue
        for romaji in _BY_LENGTH:
            if rest.startswith(romaji):
                first, medial = _SYLLABLES[romaji]
                if romaji == "tsu":
                    first = medial = tsu
                is_initial = not syllables
                syllables.append(first if is_initial and not aspirate_initial else medial)
                i += len(romaji)
                break
        else:
            raise ValueError(f"cannot parse {rest!r}")


_MACRONS = str.maketrans("āīūēōĀĪŪĒŌâîûêôÂÎÛÊÔ", "aiueoAIUEOaiueoAIUEO")


def romaji_to_hangul(name: str, aspirate_initial: bool = False, tsu: str = "쓰") -> str | None:
    """'Higashi-nihombashi' → '히가시니혼바시'. 변환할 수 없으면 None.

    aspirate_initial=True면 첫머리도 거센소리(쿠라마에), tsu="츠"면 つ를 '츠'로 적는다(관용 표기).
    장음 부호(ō, ū)는 표기법대로 적지 않는다. 'Oshiage<Skytree>'처럼 꺾쇠 안 부역명은 괄호로 붙인다.
    """
    sub = re.fullmatch(r"(.+?)\s*<(.+)>", name.strip())
    if sub:
        main = romaji_to_hangul(sub.group(1), aspirate_initial, tsu)
        extra = romaji_to_hangul(sub.group(2), aspirate_initial, tsu)
        return f"{main}({extra})" if main and extra else None
    name = name.translate(_MACRONS)
    words = []
    for word in name.strip().split():
        syllables: list[str] = []
        try:
            for seg in re.split(r"[-']", word.lower()):
                if not seg.isascii() or not seg.isalpha():
                    return None
                _segment(seg, syllables, aspirate_initial, tsu)
        except ValueError:
            return None
        words.append("".join(syllables))
    return " ".join(words) or None


def line_name_ko(name_en: str, aspirate_initial: bool = False, tsu: str = "쓰") -> str | None:
    """'Asakusa Line' → '아사쿠사선'. 'X Line' 꼴이 아니면 None (names_ko.toml로 처리)."""
    match = re.fullmatch(r"(.+) Line", name_en.strip())
    if not match:
        return None
    head = romaji_to_hangul(match.group(1), aspirate_initial, tsu)
    return f"{head}선" if head else None


def korean_variants(name_en: str, convert: Callable[..., str | None] = romaji_to_hangul) -> list[str]:
    """표기법 이름을 맨 앞에, 관용 표기(つ = 츠, 첫머리 거센소리와 그 조합)를 뒤에 둔 중복 없는 목록."""
    out: list[str] = []
    for aspirate_initial, tsu in ((False, "쓰"), (False, "츠"), (True, "쓰"), (True, "츠")):
        name = convert(name_en, aspirate_initial=aspirate_initial, tsu=tsu)
        if name and name not in out:
            out.append(name)
    return out
