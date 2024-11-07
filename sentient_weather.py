from flask import Flask, render_template, request, jsonify, url_for
from dotenv import load_dotenv
import os
from datetime import datetime
from geopy.geocoders import Nominatim
import openmeteo_requests
import requests_cache
import pandas as pd
from retry_requests import retry
from anthropic import Anthropic
from openai import OpenAI
import json

# Load environment variables
load_dotenv()

app = Flask(__name__)

def get_coordinates(city):
    """Get coordinates for a given city."""
    try:
        geolocator = Nominatim(user_agent="sentient-weather-app")
        location = geolocator.geocode(city)
        return (location.latitude, location.longitude)
    except:
        return None

def get_weather_data(latitude, longitude):
    """Get weather data from Open Meteo API."""
    try:
        # Setup the Open-Meteo API client
        cache_session = requests_cache.CachedSession('.cache', expire_after=3600)
        retry_session = retry(cache_session, retries=5, backoff_factor=0.2)
        openmeteo = openmeteo_requests.Client(session=retry_session)

        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": latitude,
            "longitude": longitude,
            "current": ["temperature_2m", "is_day", "precipitation", 
                       "weather_code", "cloud_cover", "wind_speed_10m"],
            "daily": ["weather_code", "temperature_2m_max", "temperature_2m_min",
                     "precipitation_sum", "precipitation_hours",
                     "precipitation_probability_max", "wind_speed_10m_max"]
        }

        # Make API request
        response = openmeteo.weather_api(url, params=params)[0]
        
        # Process current weather
        current = response.Current()
        current_data = {
            'temperature': current.Variables(0).Value(),
            'is_day': current.Variables(1).Value(),
            'precipitation': current.Variables(2).Value(),
            'weather_code': current.Variables(3).Value(),
            'cloud_cover': current.Variables(4).Value(),
            'wind_speed': current.Variables(5).Value(),
        }

        # Process forecast
        daily = response.Daily()
        forecast_data = []
        
        for i in range(len(daily.Variables(0).ValuesAsNumpy())):
            forecast_data.append({
                "date": pd.to_datetime(daily.Time() + i * 24 * 3600, unit="s"),
                "weather_code": daily.Variables(0).ValuesAsNumpy()[i],
                "temperature_max": daily.Variables(1).ValuesAsNumpy()[i],
                "temperature_min": daily.Variables(2).ValuesAsNumpy()[i],
                "precipitation_sum": daily.Variables(3).ValuesAsNumpy()[i],
                "precipitation_hours": daily.Variables(4).ValuesAsNumpy()[i],
                "precipitation_probability": daily.Variables(5).ValuesAsNumpy()[i],
                "wind_speed_max": daily.Variables(6).ValuesAsNumpy()[i]
            })

        return {
            'current': current_data,
            'forecast': forecast_data
        }
    except Exception as e:
        print(f"Error fetching weather data: {str(e)}")
        return None

def get_weather_description(weather_code):
    """Get weather description from code."""
    weather_codes = {
        0: 'Clear sky',
        1: 'Mainly clear',
        2: 'Partly cloudy',
        3: 'Overcast',
        45: 'Fog',
        48: 'Depositing rime fog',
        51: 'Light drizzle',
        53: 'Moderate drizzle',
        55: 'Dense drizzle',
        56: 'Light freezing drizzle',
        57: 'Dense freezing drizzle',
        61: 'Slight rain',
        63: 'Moderate rain',
        65: 'Heavy rain',
        66: 'Light freezing rain',
        67: 'Heavy freezing rain',
        71: 'Slight snow fall',
        73: 'Moderate snow fall',
        75: 'Heavy snow fall',
        77: 'Snow grains',
        80: 'Slight rain showers',
        81: 'Moderate rain showers',
        82: 'Violent rain showers',
        85: 'Slight snow showers',
        86: 'Heavy snow showers',
        95: 'Thunderstorm',
        96: 'Thunderstorm with slight hail',
        99: 'Thunderstorm with heavy hail'
    }
    return weather_codes.get(int(weather_code), 'Unknown')

def generate_color_palette(city, weather_data, weather_description):
    """Generate color palette using Anthropic API."""
    try:
        client = Anthropic()
        
        prompt = f"""
        You are a leading visual designer. You goal is to design attractive, vibrant color palettes for a weather app. 
        Each color palette is inspired by the unique atmosphere of a city and current weather conditions.
        The city and current weather conditions are:
        - location: {city}
        - weather description: {weather_description}
        - current temperature: {weather_data['current']['temperature']}
        - current precipitation: {weather_data['current']['precipitation']}
        - current cloud cover: {weather_data['current']['cloud_cover']}
        - current wind speed: {weather_data['current']['wind_speed']}
        - is it day or night: {weather_data['current']['is_day']} (0 is night, 1 is day)
        """

        messages = [{
            "role": "user",
            "content": f"""{prompt}.
            Requirements:
            - The output must be valid JSON
            - Use ONLY the following keys: dominant_color, secondary_color, accent_color
            - Each key should get a hex color code
            - Do not include any explanation or other text
            - Each value should be of type string (str)
            """
        }]

        response = client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1024,
            temperature=0.7,
            messages=messages
        )

        colors = json.loads(response.content[0].text)
        return colors

    except Exception as e:
        print(f"Error generating color palette: {str(e)}")
        return None

def generate_city_image(city, weather_description):
    """Generate and save city image using DALL-E 3."""
    try:
        import time
        import requests
        from werkzeug.utils import secure_filename

        # Get the directory of the current script
        script_dir = os.path.dirname(os.path.abspath(__file__))

        # Setup paths
        static_dir = os.path.join(script_dir, 'static', 'images')
        os.makedirs(static_dir, exist_ok=True)

        # Create filename
        timestamp = int(time.time())
        safe_city_name = secure_filename(city.lower())
        filename = f"{safe_city_name}_{timestamp}.png"

        # Generate image
        client = OpenAI()
        prompt = f"An oil painting of the most iconic scenery from {city} where the weather is {weather_description}."

        response = client.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size="1024x1024",
            quality="standard",
            n=1,
        )

        image_url = response.data[0].url

        # Download and save image
        img_response = requests.get(image_url)
        if img_response.status_code == 200:
            image_path = os.path.join(static_dir, filename)
            with open(image_path, 'wb') as f:
                f.write(img_response.content)

            # Use url_for to generate the correct URL
            return url_for('static', filename=f'images/{filename}')
            
    except Exception as e:
        print(f"Error generating city image: {str(e)}")
        return None

@app.route('/', methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        try:
            city = request.form['city']
            
            # Get coordinates
            coordinates = get_coordinates(city)
            if not coordinates:
                return render_template('index.html', error="City not found")
            
            # Get weather data
            weather_data = get_weather_data(*coordinates)
            if not weather_data:
                return render_template('index.html', error="Could not fetch weather data")
            
            # Get weather description
            weather_description = get_weather_description(weather_data['current']['weather_code'])
            
            # Generate color palette
            colors = generate_color_palette(city, weather_data, weather_description)
            if not colors:
                return render_template('index.html', error="Could not generate color palette")
            
            # Generate image
            image_path = generate_city_image(city, weather_description)
            if not image_path:
                return render_template('index.html', error="Could not generate city image")
            
            return render_template('index.html',
                                 city=city,
                                 weather_data=weather_data,
                                 weather_description=weather_description,
                                 colors=colors,
                                 image_path=image_path)
            
        except Exception as e:
            return render_template('index.html', error=str(e))
            
    return render_template('index.html')

if __name__ == '__main__':
    app.run(debug=True)
