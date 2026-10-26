import pytest

from whyuseguide_pipelines.ko_names import line_name_ko, romaji_to_hangul


@pytest.mark.parametrize(
    ("romaji", "hangul"),
    [
        ("Asakusa", "아사쿠사"),
        ("Kuramae", "구라마에"),  # 첫머리 か행은 예사소리
        ("Shinjuku", "신주쿠"),
        ("Oshiage", "오시아게"),
        ("Ryogoku", "료고쿠"),  # 장음(Ryōgoku)은 적지 않는다
        ("Nihombashi", "니혼바시"),  # 헵번식 m(b 앞) = ん
        ("Higashi-nihombashi", "히가시니혼바시"),
        ("Kishibojimmae", "기시보진마에"),
        ("Jimbocho", "진보초"),
        ("Tochomae", "도초마에"),  # 첫머리 ちょ는 조, 중간은 초
        ("Roppongi", "롯폰기"),  # 촉음 = ㅅ받침
        ("Aoyama-itchome", "아오야마잇초메"),  # tch = っ + ch
        ("Akado-shogakkomae", "아카도쇼갓코마에"),
        ("Tsukishima", "쓰키시마"),
        ("Kachidoki", "가치도키"),
        ("Ichigaya", "이치가야"),
        ("Shin-okachimachi", "신오카치마치"),  # 하이픈 앞 n은 ん (しの가 아님)
        ("Shinozaki", "시노자키"),
        ("Shin-egota", "신에고타"),
        ("Sengakuji", "센가쿠지"),
        ("Monzen-nakacho", "몬젠나카초"),
        ("Kokuritsu-kyogijo", "고쿠리쓰쿄기조"),
        ("Toden-zoshigaya", "도덴조시가야"),
        ("Gotanda", "고탄다"),
        ("Funabori", "후나보리"),
        ("Ogi-ohashi", "오기오하시"),
    ],
)
def test_romaji_to_hangul(romaji: str, hangul: str) -> None:
    assert romaji_to_hangul(romaji) == hangul


@pytest.mark.parametrize("bad", ["", "Tokyo Big Sight 2", "Café", "Xq"])
def test_unparseable_returns_none(bad: str) -> None:
    assert romaji_to_hangul(bad) is None


def test_line_name() -> None:
    assert line_name_ko("Asakusa Line") == "아사쿠사선"
    assert line_name_ko("Oedo Line") == "오에도선"
    assert line_name_ko("Nippori-Toneri Liner") is None
