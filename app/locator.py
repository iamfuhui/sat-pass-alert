"""Maidenhead grid centers; longitude is the first character of each pair."""
import re


def grid_center(value):
    grid = value.strip().upper()
    if not re.fullmatch(r'[A-R]{2}[0-9]{2}(?:[A-X]{2}(?:[0-9]{2})?)?', grid):
        raise ValueError('Maidenhead 定位须为 4、6 或 8 位，例如 OM20、OM20BO、OM20BO24')
    lon = -180 + (ord(grid[0]) - 65) * 20 + int(grid[2]) * 2
    lat = -90 + (ord(grid[1]) - 65) * 10 + int(grid[3])
    width, height = 2, 1
    if len(grid) >= 6:
        width, height = width / 24, height / 24
        lon += (ord(grid[4]) - 65) * width
        lat += (ord(grid[5]) - 65) * height
    if len(grid) == 8:
        width, height = width / 10, height / 10
        lon += int(grid[6]) * width
        lat += int(grid[7]) * height
    return grid, round(lat + height / 2, 8), round(lon + width / 2, 8)
