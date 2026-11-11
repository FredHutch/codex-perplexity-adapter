"""Deterministic synthetic pages and complete many-image regression body."""
import base64
import io
from contextlib import closing
from PIL import Image, ImageDraw
from codex_perplexity_adapter.request_size import encode_request


def many_images(size=(2500, 4000), unique=49, count=50, body_bytes=22_900_000):
    urls = []
    for index in range(unique):
        with closing(Image.new('RGB', size, 'white')) as image:
            draw = ImageDraw.Draw(image)
            for y in range(40, size[1] - 20, 200):
                draw.text((40, y), f'Synthetic page {index:02d} 724914 Dose 1.25 mg / 0.05 / YES [X]', fill='black', font_size=20)
            output = io.BytesIO(); image.save(output, 'JPEG', quality=90)
        urls.append('data:image/jpeg;base64,' + base64.b64encode(output.getvalue()).decode())
    parts = [{'type': 'input_image', 'image_url': urls[i % unique]} for i in range(count)]
    result = {'model': 'gpt-6.1-sol', 'input': [
        {'role': 'user', 'content': 'Review these synthetic pages'},
        {'type': 'function_call', 'name': 'pages', 'call_id': 'synthetic-pages', 'arguments': '{}'},
        {'type': 'function_call_output', 'call_id': 'synthetic-pages', 'output': parts}],
        'tools': [{'type': 'function', 'name': 'pages', 'description': 'Synthetic schema padding: ',
                   'parameters': {'type': 'object', 'properties': {}}}]}
    remaining = body_bytes - len(encode_request(result))
    if remaining > 0:
        result['tools'][0]['description'] += 'x' * remaining
    return result
