from protopresence.vdf import parse_vdf


def test_simple_object() -> None:
    text = '"AppState"\n{\n\t"appid"\t\t"440"\n\t"name"\t\t"Team Fortress 2"\n}\n'
    data = parse_vdf(text)
    assert data == {"AppState": {"appid": "440", "name": "Team Fortress 2"}}


def test_nested_objects() -> None:
    text = (
        '"libraryfolders"\n'
        "{\n"
        '\t"0"\n'
        "\t{\n"
        '\t\t"path"\t\t"/home/user/.steam/steam"\n'
        '\t\t"apps"\n'
        "\t\t{\n"
        '\t\t\t"440"\t\t"12345"\n'
        "\t\t}\n"
        "\t}\n"
        "}\n"
    )
    data = parse_vdf(text)
    assert data["libraryfolders"]["0"]["path"] == "/home/user/.steam/steam"
    assert data["libraryfolders"]["0"]["apps"]["440"] == "12345"


def test_comment_lines_are_ignored() -> None:
    text = '// a top-level comment\n"AppState"\n{\n\t// another comment\n\t"appid"\t\t"1"\n}\n'
    data = parse_vdf(text)
    assert data == {"AppState": {"appid": "1"}}


def test_escaped_quotes_in_values() -> None:
    text = r'"AppState"' + "\n{\n\t" + r'"name"		"Say \"Hello\""' + "\n}\n"
    data = parse_vdf(text)
    assert data["AppState"]["name"] == 'Say "Hello"'
