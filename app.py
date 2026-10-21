from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.config['SECRET_KEY'] = 'himalaya_secret_key_secure_2026'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///himalaya.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app)

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'login'

# --- MODELOS ---
class Usuario(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password_hash = db.Column(db.String(200), nullable=False)
    rol = db.Column(db.String(50), default='Socio/Operador')
    
    ventas = db.relationship('Venta', backref='usuario', lazy=True)
    gastos = db.relationship('Gasto', backref='usuario', lazy=True)
    transferencias = db.relationship('Transferencia', backref='usuario', lazy=True)
    entradas = db.relationship('EntradaInventario', backref='usuario', lazy=True)

@login_manager.user_loader
def load_user(user_id):
    return Usuario.query.get(int(user_id))

class Cliente(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    telefono = db.Column(db.String(50), nullable=True)
    direccion = db.Column(db.String(250), nullable=True)
    ventas = db.relationship('Venta', backref='cliente', lazy=True)

class Proveedor(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    telefono = db.Column(db.String(50), nullable=True)
    contacto = db.Column(db.String(150), nullable=True)
    entradas = db.relationship('EntradaInventario', backref='proveedor', lazy=True)

class Billetera(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(100), nullable=False)
    ventas = db.relationship('Venta', backref='billetera', lazy=True)
    gastos = db.relationship('Gasto', backref='billetera', lazy=True)
    transferencias_enviadas = db.relationship('Transferencia', foreign_keys='Transferencia.origen_id', backref='origen', lazy=True)
    transferencias_recibidas = db.relationship('Transferencia', foreign_keys='Transferencia.destino_id', backref='destino', lazy=True)

    @property
    def saldo(self):
        total_ventas = sum(v.total for v in self.ventas)
        total_gastos = sum(g.monto for g in self.gastos)
        total_enviadas = sum(t.monto for t in self.transferencias_enviadas)
        total_recibidas = sum(t.monto for t in self.transferencias_recibidas)
        return total_ventas - total_gastos - total_enviadas + total_recibidas

    @property
    def movimientos(self):
        lista = []
        for v in self.ventas:
            lista.append({
                'fecha': v.fecha,
                'tipo': 'Venta (Ingreso)',
                'descripcion': f"Ticket #{v.id} - Cliente: {v.cliente.nombre} (Por: {v.usuario.username})",
                'monto': v.total,
                'es_ingreso': True
            })
        for g in self.gastos:
            lista.append({
                'fecha': g.fecha,
                'tipo': 'Gasto (Salida)',
                'descripcion': f"{g.concepto} (Por: {g.usuario.username})",
                'monto': g.monto,
                'es_ingreso': False
            })
        for t in self.transferencias_enviadas:
            lista.append({
                'fecha': t.fecha,
                'tipo': 'Transferencia Enviada',
                'descripcion': f"A wallet: {t.destino.nombre} (Por: {t.usuario.username})",
                'monto': t.monto,
                'es_ingreso': False
            })
        for t in self.transferencias_recibidas:
            lista.append({
                'fecha': t.fecha,
                'tipo': 'Transferencia Recibida',
                'descripcion': f"Desde wallet: {t.origen.nombre}",
                'monto': t.monto,
                'es_ingreso': True
            })
        lista.sort(key=lambda x: x['fecha'], reverse=True)
        return lista

class Producto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nombre = db.Column(db.String(150), nullable=False)
    precio = db.Column(db.Float, nullable=False)
    stock_actual = db.Column(db.Integer, default=0)
    detalles_venta = db.relationship('DetalleVenta', backref='producto', lazy=True)
    detalles_entrada = db.relationship('DetalleEntrada', backref='producto', lazy=True)

class Venta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=False)
    billetera_id = db.Column(db.Integer, db.ForeignKey('billetera.id'), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    total = db.Column(db.Float, nullable=False, default=0.0)
    fecha = db.Column(db.DateTime, nullable=False)
    detalles = db.relationship('DetalleVenta', backref='venta', cascade="all, delete-orphan", lazy=True)

class DetalleVenta(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    venta_id = db.Column(db.Integer, db.ForeignKey('venta.id'), nullable=False)
    producto_id = db.Column(db.Integer, db.ForeignKey('producto.id'), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    precio_unitario = db.Column(db.Float, nullable=False)

class EntradaInventario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    proveedor_id = db.Column(db.Integer, db.ForeignKey('proveedor.id'), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    referencia = db.Column(db.String(150), nullable=True)
    fecha = db.Column(db.DateTime, nullable=False)
    detalles = db.relationship('DetalleEntrada', backref='entrada', cascade="all, delete-orphan", lazy=True)

class DetalleEntrada(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    entrada_id = db.Column(db.Integer, db.ForeignKey('entrada_inventario.id'), nullable=False)
    producto_id = db.Column(db.Integer, db.ForeignKey('producto.id'), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)

class Gasto(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    concepto = db.Column(db.String(200), nullable=False)
    monto = db.Column(db.Float, nullable=False)
    billetera_id = db.Column(db.Integer, db.ForeignKey('billetera.id'), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    fecha = db.Column(db.DateTime, nullable=False)

class Transferencia(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    origen_id = db.Column(db.Integer, db.ForeignKey('billetera.id'), nullable=False)
    destino_id = db.Column(db.Integer, db.ForeignKey('billetera.id'), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    monto = db.Column(db.Float, nullable=False)
    fecha = db.Column(db.DateTime, nullable=False)

with app.app_context():
    db.create_all()
    # Crear un usuario administrador por defecto si no existe ninguno
    if not Usuario.query.first():
        admin = Usuario(username='admin', password_hash=generate_password_hash('123456'), rol='Administrador')
        db.create_all()
        db.session.add(admin)
        db.session.commit()

# --- RUTAS DE AUTENTICACIÓN ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = Usuario.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            return redirect(url_for('dashboard'))
        flash('Usuario o contraseña incorrectos', 'error')
    return render_template('login.html', store_name="Himalaya Store SC")

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

@app.route('/usuarios', methods=['GET', 'POST'])
@login_required
def gestionar_usuarios():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        rol = request.form.get('rol', 'Socio/Operador')
        if username and password:
            if not Usuario.query.filter_by(username=username).first():
                nuevo_u = Usuario(username=username, password_hash=generate_password_hash(password), rol=rol)
                db.session.add(nuevo_u)
                db.session.commit()
                flash('Usuario creado exitosamente', 'success')
            else:
                flash('El nombre de usuario ya existe', 'error')
        return redirect(url_for('gestionar_usuarios'))
    
    usuarios = Usuario.query.all()
    return render_template('usuarios.html', store_name="Himalaya Store SC", usuarios=usuarios)

# --- RUTAS PRINCIPALES (PROTEGIDAS) ---

@app.route('/')
@login_required
def dashboard():
    productos = Producto.query.all()
    billeteras = Billetera.query.all()
    clientes = Cliente.query.all()
    proveedores = Proveedor.query.all()
    ventas = Venta.query.order_by(Venta.fecha.desc()).limit(5).all()
    
    total_capital = sum(b.saldo for b in billeteras)
    total_inventario = sum(p.precio * p.stock_actual for p in productos)
    
    return render_template('dashboard.html', 
                           store_name="Himalaya Store SC",
                           billeteras=billeteras,
                           total_capital=total_capital,
                           total_inventario=total_inventario,
                           total_clientes=len(clientes),
                           total_proveedores=len(proveedores),
                           total_productos=len(productos),
                           ventas=ventas)

@app.route('/inventario')
@login_required
def inventario():
    productos = Producto.query.all()
    proveedores = Proveedor.query.all()
    entradas = EntradaInventario.query.order_by(EntradaInventario.fecha.desc()).all()
    fecha_hoy = datetime.now().strftime('%Y-%m-%d')
    return render_template('inventario.html', store_name="Himalaya Store SC", productos=productos, proveedores=proveedores, entradas=entradas, fecha_hoy=fecha_hoy)

@app.route('/ventas')
@login_required
def ventas():
    productos = Producto.query.all()
    billeteras = Billetera.query.all()
    clientes = Cliente.query.all()
    lista_ventas = Venta.query.order_by(Venta.fecha.desc()).all()
    fecha_hoy = datetime.now().strftime('%Y-%m-%d')
    return render_template('ventas.html', store_name="Himalaya Store SC", productos=productos, billeteras=billeteras, clientes=clientes, ventas=lista_ventas, fecha_hoy=fecha_hoy)

@app.route('/clientes')
@login_required
def clientes():
    lista_clientes = Cliente.query.all()
    return render_template('clientes.html', store_name="Himalaya Store SC", clientes=lista_clientes)

@app.route('/proveedores')
@login_required
def proveedores():
    lista_proveedores = Proveedor.query.all()
    return render_template('proveedores.html', store_name="Himalaya Store SC", proveedores=lista_proveedores)

@app.route('/finanzas')
@login_required
def finanzas():
    billeteras = Billetera.query.all()
    gastos = Gasto.query.order_by(Gasto.fecha.desc()).all()
    transferencias = Transferencia.query.order_by(Transferencia.fecha.desc()).all()
    fecha_hoy = datetime.now().strftime('%Y-%m-%d')
    wallet_seleccionada = request.args.get('wallet_id', type=int)
    
    selected_wallet = Billetera.query.get(wallet_seleccionada) if wallet_seleccionada else (billeteras[0] if billeteras else None)

    return render_template('finanzas.html', 
                           store_name="Himalaya Store SC", 
                           billeteras=billeteras, 
                           gastos=gastos, 
                           transferencias=transferencias, 
                           fecha_hoy=fecha_hoy,
                           selected_wallet=selected_wallet)

# --- ACCIONES (CON EMISIÓN DE NOTIFICACIONES WEBSOCKET) ---

@app.route('/agregar_cliente', methods=['POST'])
@login_required
def agregar_cliente():
    nombre = request.form.get('nombre_cliente')
    telefono = request.form.get('telefono_cliente')
    direccion = request.form.get('direccion_cliente')
    if nombre:
        db.session.add(Cliente(nombre=nombre, telefono=telefono, direccion=direccion))
        db.session.commit()
    return redirect(url_for('clientes'))

@app.route('/agregar_proveedor', methods=['POST'])
@login_required
def agregar_proveedor():
    nombre = request.form.get('nombre_proveedor')
    telefono = request.form.get('telefono_proveedor')
    contacto = request.form.get('contacto_proveedor')
    if nombre:
        db.session.add(Proveedor(nombre=nombre, telefono=telefono, contacto=contacto))
        db.session.commit()
    return redirect(url_for('proveedores'))

@app.route('/agregar_producto', methods=['POST'])
@login_required
def agregar_producto():
    nombre = request.form.get('nombre')
    precio = float(request.form.get('precio', 0))
    stock = int(request.form.get('stock', 0))
    if nombre:
        db.session.add(Producto(nombre=nombre, precio=precio, stock_actual=stock))
        db.session.commit()
    return redirect(url_for('inventario'))

@app.route('/registrar_entrada_inventario', methods=['POST'])
@login_required
def registrar_entrada_inventario():
    proveedor_id = request.form.get('proveedor_id')
    referencia = request.form.get('referencia')
    fecha_str = request.form.get('fecha')
    productos_ids = request.form.getlist('producto_id[]')
    cantidades = request.form.getlist('cantidad[]')
    
    fecha_entrada = datetime.strptime(fecha_str, '%Y-%m-%d') if fecha_str else datetime.utcnow()
    if not proveedor_id or not productos_ids:
        return redirect(url_for('inventario'))

    nueva_entrada = EntradaInventario(proveedor_id=int(proveedor_id), usuario_id=current_user.id, referencia=referencia, fecha=fecha_entrada)
    db.session.add(nueva_entrada)
    db.session.flush()

    for p_id, cant in zip(productos_ids, cantidades):
        if p_id and cant:
            producto = Producto.query.get(int(p_id))
            cantidad = int(cant)
            if producto and cantidad > 0:
                producto.stock_actual += cantidad
                db.session.add(DetalleEntrada(entrada_id=nueva_entrada.id, producto_id=producto.id, cantidad=cantidad))

    db.session.commit()
    
    # Notificar a todos los usuarios conectados
    socketio.emit('notificacion_global', {
        'titulo': '📦 Nuevo Pedido Recibido',
        'mensaje': f'{current_user.username} registró una entrada de inventario (Ref: {referencia}).'
    })
    
    return redirect(url_for('inventario'))

@app.route('/agregar_billetera', methods=['POST'])
@login_required
def agregar_billetera():
    nombre = request.form.get('nombre_billetera')
    if nombre:
        db.session.add(Billetera(nombre=nombre))
        db.session.commit()
    return redirect(url_for('finanzas'))

@app.route('/registrar_venta', methods=['POST'])
@login_required
def registrar_venta():
    cliente_id = request.form.get('cliente_id')
    billetera_id = request.form.get('billetera_id')
    fecha_str = request.form.get('fecha')
    productos_ids = request.form.getlist('producto_id[]')
    cantidades = request.form.getlist('cantidad[]')
    
    fecha_venta = datetime.strptime(fecha_str, '%Y-%m-%d') if fecha_str else datetime.utcnow()
    if not cliente_id or not billetera_id or not productos_ids:
        return redirect(url_for('ventas'))

    nueva_venta = Venta(cliente_id=int(cliente_id), billetera_id=int(billetera_id), usuario_id=current_user.id, total=0.0, fecha=fecha_venta)
    db.session.add(nueva_venta)
    db.session.flush()

    total_venta = 0.0
    for p_id, cant in zip(productos_ids, cantidades):
        if p_id and cant:
            producto = Producto.query.get(int(p_id))
            cantidad = int(cant)
            if producto and cantidad > 0 and producto.stock_actual >= cantidad:
                producto.stock_actual -= cantidad
                subtotal = producto.precio * cantidad
                total_venta += subtotal
                db.session.add(DetalleVenta(venta_id=nueva_venta.id, producto_id=producto.id, cantidad=cantidad, precio_unitario=producto.precio))

    nueva_venta.total = total_venta
    db.session.commit()

    # Notificar a todos los usuarios conectados
    socketio.emit('notificacion_global', {
        'titulo': '🛒 Nueva Venta Registrada',
        'mensaje': f'{current_user.username} realizó una venta por ${total_venta:.2f} cobrada en {nueva_venta.billetera.nombre}.'
    })

    return redirect(url_for('ventas'))

@app.route('/registrar_gasto', methods=['POST'])
@login_required
def registrar_gasto():
    concepto = request.form.get('concepto')
    monto = float(request.form.get('monto', 0))
    billetera_id = int(request.form.get('billetera_id'))
    fecha_str = request.form.get('fecha')
    
    fecha_gasto = datetime.strptime(fecha_str, '%Y-%m-%d') if fecha_str else datetime.utcnow()
    if concepto and monto > 0 and billetera_id:
        gasto = Gasto(concepto=concepto, monto=monto, billetera_id=billetera_id, usuario_id=current_user.id, fecha=fecha_gasto)
        db.session.add(gasto)
        db.session.commit()
        
        # Notificar a todos
        socketio.emit('notificacion_global', {
            'titulo': '💸 Gasto Registrado',
            'mensaje': f'{current_user.username} registró un gasto de ${monto:.2f} ({concepto}).'
        })
        
    return redirect(url_for('finanzas'))

@app.route('/registrar_transferencia', methods=['POST'])
@login_required
def registrar_transferencia():
    origen_id = int(request.form.get('origen_id'))
    destino_id = int(request.form.get('destino_id'))
    monto = float(request.form.get('monto', 0))
    fecha_str = request.form.get('fecha')
    
    fecha_trans = datetime.strptime(fecha_str, '%Y-%m-%d') if fecha_str else datetime.utcnow()
    if origen_id and destino_id and origen_id != destino_id and monto > 0:
        trans = Transferencia(origen_id=origen_id, destino_id=destino_id, monto=monto, usuario_id=current_user.id, fecha=fecha_trans)
        db.session.add(trans)
        db.session.commit()
        
        # Notificar a todos
        socketio.emit('notificacion_global', {
            'titulo': '🔄 Transferencia entre Wallets',
            'mensaje': f'{current_user.username} transfirió ${monto:.2f} entre billeteras.'
        })
        
    return redirect(url_for('finanzas'))

@app.route('/venta/<int:id>')
@login_required
def detalle_venta(id):
    venta = Venta.query.get_or_404(id)
    return render_template('detalle_venta.html', store_name="Himalaya Store SC", venta=venta)

if __name__ == '__main__':
    socketio.run(app, debug=True, port=5000)