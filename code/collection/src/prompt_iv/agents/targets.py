from __future__ import annotations


MEETING_YEAR_RANGES = [
    (268, None, 2025),
    (260, 267, 2024),
    (252, 259, 2023),
    (244, 251, 2022),
    (236, 243, 2021),
    (228, 235, 2020),
    (220, 227, 2019),
    (212, 219, 2018),
    (204, 211, 2017),
    (196, 203, 2016),
    (188, 195, 2015),
    (180, 187, 2014),
    (172, 179, 2013),
    (164, 171, 2012),
    (156, 163, 2011),
    (148, 155, 2010),
    (140, 147, 2009),
    (132, 139, 2008),
    (124, 131, 2007),
    (116, 123, 2006),
    (104, 115, 2005),
    (92, 103, 2004),
    (80, 91, 2003),
]

INFLATION_TARGETS = {
    2025: 3.0,
    2024: 3.0,
    2023: 3.25,
    2022: 3.5,
    2021: 3.75,
    2020: 4.0,
    2019: 4.25,
    2018: 4.5,
    2017: 4.5,
    2016: 4.5,
    2015: 4.5,
    2014: 4.5,
    2013: 4.5,
    2012: 4.5,
    2011: 4.5,
    2010: 4.5,
    2009: 4.5,
    2008: 4.5,
    2007: 4.5,
    2006: 4.5,
    2005: 4.5,
    2004: 3.75,
    2003: 3.25,
}


def inferred_year_for_meeting(meeting_number: int) -> int:
    for start, end, year in MEETING_YEAR_RANGES:
        if end is None:
            if meeting_number >= start:
                return year
        elif start <= meeting_number <= end:
            return year
    return 1999


def inflation_target_for_meeting(meeting_number: int) -> float:
    return INFLATION_TARGETS.get(inferred_year_for_meeting(meeting_number), 4.5)
