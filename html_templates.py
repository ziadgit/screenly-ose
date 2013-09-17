# -*- coding: utf8 -*-


def black_page():
    filepath = "/tmp/screenly_html/black_page.html"
    html = """
<html>
  <head>
    <style>
      body { background:#000 center no-repeat; }
    </style>
  </head>
  <!-- Just a black page -->
</html>
"""
    with open(filepath, 'w') as f:
        f.write(html)
    return filepath
