from flask import Flask, render_template

app = Flask(__name__)

# Ruta principal de prueba
@app.route('/')
def index():
    return render_template('index.html', store_name="Himalaya Store SC")

if __name__ == '__main__':
    # debug=True permite que el servidor se recargue solo al guardar cambios en el código
    app.run(debug=True, port=5000)