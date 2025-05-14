import logging
import geopandas as gpd
import colorsys
import random
import re
from shapely import Polygon, Point
import xml.etree.ElementTree as ET

LOGGER = logging.getLogger('map_utils')

# Style function for the bounding box GeoJSON layer
def style_function(feature):
    return {
        'fillColor': '#808080',  
        'color': '#000000',      
        'weight': 4,              
        'fillOpacity': 0.3        
    }
# Function to generate random hex color
def random_color():
    return "#{:02x}{:02x}{:02x}".format(random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))

# Function to print text with color in console (ANSI escape code)
def print_colored_text(text, color):
    # Escape sequence for colored text
    print(f"\033[38;2;{color[0]};{color[1]};{color[2]}m{text}\033[39m")

# 
def hsl_distinct_colors(n):
    colors = []
    for i in range(n):
        # Generate colors with different hues
        hue = i / float(n)  
        color = colorsys.hsv_to_rgb(hue, 1.0, 1.0)  
        # Convert from RGB (0-1) to hex (#RRGGBB)
        rgb = [int(c * 255) for c in color]
        hex_color = "#{:02x}{:02x}{:02x}".format(*rgb)
        colors.append(hex_color)
    return colors

def spread_rgb_colors(n):
    colors = []
    # Divide the color space into n parts
    step = 255 // n  
    for i in range(n):
        # Spread out the color values across the RGB spectrum
        r = (i * step) % 256
        g = ((i + 1) * step) % 256
        b = ((i + 2) * step) % 256
        hex_color = "#{:02x}{:02x}{:02x}".format(r, g, b)
        colors.append(hex_color)
    return colors

def hsl_distinct_colors_improved(num_colors):
    colors = []
    
    for i in range(num_colors):
        # Set Hue (H) to a random value, excluding extremes like 0° (red) and 60° (yellow)
        hue = (i * 360 / num_colors) % 360
        
        # Set Saturation (S) to a high value (e.g., 70%) for vivid colors
        saturation = random.randint(60, 80)  
        
        # Set Lightness (L) to a lower value to avoid bright, light colors like yellow (range 30-50%)
        lightness = random.randint(30, 50)  

        # Convert HSL to RGB using the colorsys library
        r, g, b = colorsys.hls_to_rgb(hue / 360, lightness / 100, saturation / 100)

        # Convert RGB to hex format (RGB values are in [0, 1], so multiply by 255)
        hex_color = "#{:02x}{:02x}{:02x}".format(int(r * 255), int(g * 255), int(b * 255))
        colors.append(hex_color)
    
    return colors