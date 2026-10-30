from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from flask_socketio import SocketIO, emit
from werkzeug.security import generate_password_hash, check_password_hash

import os

app = Flask(__name__)
app.config['SECRET_KEY'] = 'himalaya_secret_key_secure_2026'

# 🛠️ NUEVO: Detectar si el disco persistente de Render está montado en /var/data
if os.path.exists('/var/data'):
    db_path = '/var/data/himalaya.db'
else:
    db_path = 'himalaya.db'

app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
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
    debe_cambiar_clave = db.Column(db.Boolean, default=False) # 👈 Bandera para forzar cambio de clave inicial
    
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
    saldo_inicial = db.Column(db.Float, default=0.0)
    fecha_creacion = db.Column(db.DateTime, default=datetime.utcnow)
    
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
        return self.saldo_inicial + total_ventas - total_gastos - total_enviadas + total_recibidas

    @property
    def movimientos(self):
        lista = []
        if self.saldo_inicial > 0:
            lista.append({
                'fecha': self.fecha_creacion,
                'tipo': 'Capital Inicial (Apertura)',
                'descripcion': 'Saldo con el que arrancó la billetera (Negocio en marcha)',
                'monto': self.saldo_inicial,
                'es_ingreso': True
            })

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
            detalle_nota = f" - Nota: {t.comentario}" if t.comentario else ""
            lista.append({
                'fecha': t.fecha,
                'tipo': 'Transferencia Enviada',
                'descripcion': f"A wallet: {t.destino.nombre}{detalle_nota}",
                'monto': t.monto,
                'es_ingreso': False
            })
        for t in self.transferencias_recibidas:
            detalle_nota = f" - Nota: {t.comentario}" if t.comentario else ""
            lista.append({
                'fecha': t.fecha,
                'tipo': 'Transferencia Recibida',
                'descripcion': f"Desde wallet: {t.origen.nombre}{detalle_nota}",
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
    comentario = db.Column(db.String(250), nullable=True)
    fecha = db.Column(db.DateTime, nullable=False)

class Cotizacion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cliente_id = db.Column(db.Integer, db.ForeignKey('cliente.id'), nullable=False)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    estado = db.Column(db.String(50), default='Pendiente')
    total = db.Column(db.Float, nullable=False, default=0.0)
    fecha = db.Column(db.DateTime, nullable=False)
    validez_dias = db.Column(db.Integer, default=7)
    cliente = db.relationship('Cliente', backref='cotizaciones', lazy=True)
    detalles = db.relationship('DetalleCotizacion', backref='cotizacion', cascade="all, delete-orphan", lazy=True)

class DetalleCotizacion(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    cotizacion_id = db.Column(db.Integer, db.ForeignKey('cotizacion.id'), nullable=False)
    producto_id = db.Column(db.Integer, db.ForeignKey('producto.id'), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    precio_unitario = db.Column(db.Float, nullable=False)
    producto = db.relationship('Producto', backref='detalles_cotizacion', lazy=True)

class CierreMensual(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    anio = db.Column(db.Integer, nullable=False)
    mes = db.Column(db.Integer, nullable=False)
    fecha_cierre = db.Column(db.DateTime, default=datetime.utcnow)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), nullable=False)
    total_ventas = db.Column(db.Float, default=0.0)
    total_gastos = db.Column(db.Float, default=0.0)
    usuario = db.relationship('Usuario', backref='cierres_realizados', lazy=True)

class SalidaInventario(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fecha = db.Column(db.DateTime, default=datetime.utcnow)
    concepto = db.Column(db.String(255), nullable=False)
    observacion = db.Column(db.Text, nullable=True)
    detalles = db.relationship('DetalleSalida', backref='salida', cascade='all, delete-orphan')

class DetalleSalida(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    salida_id = db.Column(db.Integer, db.ForeignKey('salida_inventario.id'), nullable=False)
    producto_id = db.Column(db.Integer, db.ForeignKey('producto.id'), nullable=False)
    cantidad = db.Column(db.Integer, nullable=False)
    producto = db.relationship('Producto')    

with app.app_context():
    db.create_all()
    if not Usuario.query.first():
        # Admin inicial creado con clave genérica y forzado a cambiarla en su primer login
        admin = Usuario(username='admin', password_hash=generate_password_hash('123456'), rol='Administrador', debe_cambiar_clave=True)
        db.session.add(admin)
        db.session.commit()

# --- INTERCEPTOR DE SEGURIDAD: OBLIGAR A CAMBIAR CLAVE ---
@app.before_request
def verificar_cambio_clave():
    if current_user.is_authenticated and current_user.debe_cambiar_clave:
        if request.endpoint not in ['cambiar_clave', 'logout', 'static']:
            return redirect(url_for('cambiar_clave'))

# --- RUTAS DE AUTENTICACIÓN Y PERFIL ---

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user = Usuario.query.filter_by(username=username).first()
        
        if user and check_password_hash(user.password_hash, password):
            login_user(user)
            if user.debe_cambiar_clave:
                return redirect(url_for('cambiar_clave'))
            return redirect(url_for('dashboard'))
        flash('Usuario o contraseña incorrectos', 'error')
    return render_template('login.html', store_name="Himalaya Store SC")

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('login'))

# Ruta para que CUALQUIER usuario logueado pueda cambiar su propio nombre o contraseña
@app.route('/cambiar_clave', methods=['GET', 'POST'])
@login_required
def cambiar_clave():
    if request.method == 'POST':
        nuevo_nombre = request.form.get('username')
        password_actual = request.form.get('password_actual')
        password_nuevo = request.form.get('password_nuevo')
        
        if not check_password_hash(current_user.password_hash, password_actual):
            flash('La contraseña actual es incorrecta.', 'error')
            return redirect(url_for('cambiar_clave'))
            
        if nuevo_nombre:
            current_user.username = nuevo_nombre
            
        if password_nuevo:
            current_user.password_hash = generate_password_hash(password_nuevo)
            
        current_user.debe_cambiar_clave = False # Ya cumplió con el requisito
        db.session.commit()
        
        flash('¡Credenciales actualizadas exitosamente!', 'success')
        return redirect(url_for('dashboard'))
        
    return render_template('cambiar_clave.html', store_name="Himalaya Store SC")

# --- MÓDULO DE USUARIOS (EXCLUSIVO ADMINISTRADOR) ---
@app.route('/usuarios', methods=['GET', 'POST'])
@login_required
def gestionar_usuarios():
    # 🔒 Bloqueo estricto: Si no es Administrador, fuera de aquí
    if current_user.rol != 'Administrador':
        flash('Acceso denegado. No tienes permisos de administrador.', 'error')
        return redirect(url_for('dashboard'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        rol = request.form.get('rol', 'Socio/Operador')
        if username and password:
            if not Usuario.query.filter_by(username=username).first():
                nuevo_u = Usuario(
                    username=username, 
                    password_hash=generate_password_hash(password), 
                    rol=rol,
                    debe_cambiar_clave=True # Los nuevos usuarios creados también deben cambiar su clave inicial
                )
                db.session.add(nuevo_u)
                db.session.commit()
                flash('Usuario creado exitosamente', 'success')
            else:
                flash('El nombre de usuario ya existe', 'error')
        return redirect(url_for('gestionar_usuarios'))
    
    usuarios = Usuario.query.all()
    return render_template('usuarios.html', store_name="Himalaya Store SC", usuarios=usuarios)

@app.route('/usuario/<int:id>/reset_password', methods=['POST'])
@login_required
def reset_password(id):
    # 🔒 Solo el Administrador puede hacer esto
    if current_user.rol != 'Administrador':
        flash('Acceso denegado. No tienes permisos.', 'error')
        return redirect(url_for('dashboard'))
        
    usuario = Usuario.query.get_or_404(id)
    nueva_clave_temporal = request.form.get('nueva_clave', '123456') # Clave por defecto si no especifica otra
    
    usuario.password_hash = generate_password_hash(nueva_clave_temporal)
    usuario.debe_cambiar_clave = True # 👈 ¡Importante! Lo obliga a cambiarla al volver a entrar
    db.session.commit()
    
    flash(f'Contraseña restablecida para {usuario.username}. Clave temporal: {nueva_clave_temporal}', 'success')
    return redirect(url_for('gestionar_usuarios'))    

# --- RUTAS PRINCIPALES (PROTEGIDAS) ---

@app.route('/')
@login_required
def dashboard():
    from datetime import datetime
    
    productos = Producto.query.all()
    billeteras = Billetera.query.all()
    clientes = Cliente.query.all()
    proveedores = Proveedor.query.all()
    ventas = Venta.query.order_by(Venta.fecha.desc()).limit(5).all()
    
    total_capital = sum(b.saldo for b in billeteras)
    total_inventario = sum(p.precio * p.stock_actual for p in productos)
    
    # 📊 NUEVO: Calcular cantidad total de unidades físicas en inventario
    cantidad_total_stock = sum(p.stock_actual for p in productos)
    
    # 📊 NUEVO: Calcular monto total de ventas del mes actual
    mes_actual = datetime.now().month
    anio_actual = datetime.now().year
    ventas_mes = Venta.query.filter(
        db.extract('month', Venta.fecha) == mes_actual,
        db.extract('year', Venta.fecha) == anio_actual
    ).all()
    total_ventas_mes = sum(v.total for v in ventas_mes)
    
    return render_template('dashboard.html', 
                           store_name="Himalaya Store SC",
                           billeteras=billeteras,
                           total_capital=total_capital,
                           total_inventario=total_inventario,
                           total_ventas_mes=total_ventas_mes,          # 👈 Pasamos esta variable
                           cantidad_total_stock=cantidad_total_stock,  # 👈 Y esta variable
                           total_proveedores=len(proveedores),
                           ventas=ventas)

@app.route('/productos', methods=['GET'])
@login_required
def productos():
    busqueda = request.args.get('q', '').strip()
    if busqueda:
        lista_productos = Producto.query.filter(Producto.nombre.ilike(f'%{busqueda}%')).all()
    else:
        lista_productos = Producto.query.all()
        
    return render_template('productos.html', store_name="Himalaya Store SC", productos=lista_productos, busqueda=busqueda)

@app.route('/inventario')
@login_required
def inventario():
    productos = Producto.query.all()
    proveedores = Proveedor.query.all()
    entradas = EntradaInventario.query.order_by(EntradaInventario.fecha.desc()).all()
    salidas = SalidaInventario.query.order_by(SalidaInventario.fecha.desc()).all()
    fecha_hoy = datetime.now().strftime('%Y-%m-%d')
    return render_template('inventario.html', store_name="Himalaya Store SC", productos=productos, proveedores=proveedores, entradas=entradas, salidas=salidas, fecha_hoy=fecha_hoy)

@app.route('/inventario/imprimir')
@login_required
def imprimir_inventario():
    productos = Producto.query.filter(Producto.stock_actual > 0).all()
    fecha_hoy = datetime.now().strftime('%Y-%m-%d')
    return render_template('imprimir_inventario.html', store_name="Himalaya Store SC", productos=productos, fecha_hoy=fecha_hoy)

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


# ==========================================
# --- MÓDULO FINANCIERO SEPARADO ---
# ==========================================

@app.route('/wallets')
@login_required
def wallets():
    billeteras = Billetera.query.all()
    fecha_hoy = datetime.now().strftime('%Y-%m-%d')
    return render_template('wallets.html', 
                           store_name="Himalaya Store SC", 
                           billeteras=billeteras, 
                           fecha_hoy=fecha_hoy)

@app.route('/gastos')
@login_required
def gastos():
    billeteras = Billetera.query.all()
    lista_gastos = Gasto.query.order_by(Gasto.fecha.desc()).all()
    fecha_hoy = datetime.now().strftime('%Y-%m-%d')
    return render_template('gastos.html', 
                           store_name="Himalaya Store SC", 
                           billeteras=billeteras, 
                           gastos=lista_gastos, 
                           fecha_hoy=fecha_hoy)

@app.route('/cierres')
@login_required
def cierres():
    lista_cierres = CierreMensual.query.order_by(CierreMensual.anio.desc(), CierreMensual.mes.desc()).all()
    fecha_hoy = datetime.now()
    return render_template('cierres.html', store_name="Himalaya Store SC", cierres=lista_cierres, fecha_hoy=fecha_hoy)

@app.route('/wallet/<int:id>')
@login_required
def detalle_wallet(id):
    wallet = Billetera.query.get_or_404(id)
    return render_template('detalle_wallet.html', store_name="Himalaya Store SC", wallet=wallet)


# --- ACCIONES Y PROCESAMIENTO (CON WEBSOCKETS) ---

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
    try:
        precio = float(request.form.get('precio', 0))
        stock = int(request.form.get('stock', 0))
    except ValueError:
        precio = 0.0
        stock = 0

    if nombre:
        db.session.add(Producto(nombre=nombre, precio=precio, stock_actual=stock))
        db.session.commit()
    return redirect(url_for('productos'))

@app.route('/editar_producto/<int:id>', methods=['POST'])
@login_required
def editar_producto(id):
    prod = Producto.query.get_or_404(id)
    prod.nombre = request.form.get('nombre', prod.nombre)
    try:
        prod.precio = float(request.form.get('precio', prod.precio))
    except ValueError:
        pass
    
    db.session.commit()
    flash('Producto actualizado correctamente.', 'success')
    return redirect(url_for('productos'))

@app.route('/registrar_venta', methods=['POST'])
@login_required
def registrar_venta():
    cliente_id = request.form.get('cliente_id')
    billetera_id = request.form.get('billetera_id')
    fecha_str = request.form.get('fecha')
    
    productos_ids = request.form.getlist('producto_id[]')
    cantidades = request.form.getlist('cantidad[]')
    precios_manuales = request.form.getlist('precio_unitario[]') # 👈 Capturamos los precios manuales
    
    fecha_venta = datetime.strptime(fecha_str, '%Y-%m-%d') if fecha_str else datetime.utcnow()
    if not cliente_id or not billetera_id or not productos_ids:
        flash('Faltan datos obligatorios para registrar la venta.', 'error')
        return redirect(url_for('ventas'))

    # Validar stock primero
    for p_id, cant in zip(productos_ids, cantidades):
        if p_id and cant:
            producto = Producto.query.get(int(p_id))
            cantidad = int(cant)
            if producto and producto.stock_actual < cantidad:
                flash(f'Stock insuficiente para el producto: {producto.nombre}', 'error')
                return redirect(url_for('ventas'))

    # Crear la venta principal
    nueva_venta = Venta(
        cliente_id=int(cliente_id),
        billetera_id=int(billetera_id),
        usuario_id=current_user.id,
        total=0.0,
        fecha=fecha_venta
    )
    db.session.add(nueva_venta)
    db.session.flush()

    total_venta = 0.0
    
    # Recorrer los productos usando zip para asociar ID, cantidad y precio manual
    for p_id, cant, p_manual in zip(productos_ids, cantidades, precios_manuales):
        if p_id and cant:
            producto = Producto.query.get(int(p_id))
            cantidad = int(cant)
            
            # Usar el precio manual ingresado, o el de la base de datos si viene vacío
            try:
                precio_final = float(p_manual) if p_manual else producto.precio
            except ValueError:
                precio_final = producto.precio

            if producto and cantidad > 0:
                subtotal = precio_final * cantidad
                total_venta += subtotal
                producto.stock_actual -= cantidad
                
                db.session.add(DetalleVenta(
                    venta_id=nueva_venta.id,
                    producto_id=producto.id,
                    cantidad=cantidad,
                    precio_unitario=precio_final # 👈 Guardamos el precio manual en el detalle
                ))

    nueva_venta.total = total_venta
    db.session.commit()

    socketio.emit('notificacion_global', {
        'titulo': '🛒 Nueva Venta Registrada',
        'mensaje': f'{current_user.username} registró una venta por ${total_venta:.2f}.'
    })

    return redirect(url_for('ventas'))

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
    
    socketio.emit('notificacion_global', {
        'titulo': '📦 Nuevo Pedido Recibido',
        'mensaje': f'{current_user.username} registró una entrada de inventario (Ref: {referencia}).'
    })
    
    return redirect(url_for('inventario'))

@app.route('/agregar_billetera', methods=['POST'])
@login_required
def agregar_billetera():
    nombre = request.form.get('nombre_billetera')
    try:
        saldo_inicial = float(request.form.get('saldo_inicial', 0.0))
    except ValueError:
        saldo_inicial = 0.0

    if nombre:
        nueva_w = Billetera(nombre=nombre, saldo_inicial=saldo_inicial, fecha_creacion=datetime.utcnow())
        db.session.add(nueva_w)
        db.session.commit()
    return redirect(url_for('wallets'))

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
        
        socketio.emit('notificacion_global', {
            'titulo': '💸 Gasto Registrado',
            'mensaje': f'{current_user.username} registró un gasto de ${monto:.2f} ({concepto}).'
        })
        
    return redirect(url_for('gastos'))

@app.route('/registrar_transferencia', methods=['POST'])
@login_required
def registrar_transferencia():
    origen_id = int(request.form.get('origen_id'))
    destino_id = int(request.form.get('destino_id'))
    monto = float(request.form.get('monto', 0))
    comentario = request.form.get('comentario', '').strip()
    fecha_str = request.form.get('fecha')
    
    fecha_trans = datetime.strptime(fecha_str, '%Y-%m-%d') if fecha_str else datetime.utcnow()
    if origen_id and destino_id and origen_id != destino_id and monto > 0:
        trans = Transferencia(
            origen_id=origen_id, 
            destino_id=destino_id, 
            monto=monto, 
            usuario_id=current_user.id, 
            comentario=comentario, 
            fecha=fecha_trans
        )
        db.session.add(trans)
        db.session.commit()
        
        socketio.emit('notificacion_global', {
            'titulo': '🔄 Transferencia entre Wallets',
            'mensaje': f'{current_user.username} transfirió ${monto:.2f} entre billeteras.'
        })
        
    return redirect(url_for('wallets'))

@app.route('/venta/<int:id>')
@login_required
def detalle_venta(id):
    venta = Venta.query.get_or_404(id)
    return render_template('detalle_venta.html', store_name="Himalaya Store SC", venta=venta)

@app.route('/cotizaciones')
@login_required
def cotizaciones():
    productos = Producto.query.all()
    clientes = Cliente.query.all()
    billeteras = Billetera.query.all()
    lista_cotizaciones = Cotizacion.query.filter_by(estado='Pendiente').order_by(Cotizacion.fecha.desc()).all()
    fecha_hoy = datetime.now().strftime('%Y-%m-%d')
    return render_template('cotizaciones.html', store_name="Himalaya Store SC", productos=productos, clientes=clientes, billeteras=billeteras, cotizaciones=lista_cotizaciones, fecha_hoy=fecha_hoy)

@app.route('/registrar_cotizacion', methods=['POST'])
@login_required
def registrar_cotizacion():
    cliente_id = request.form.get('cliente_id')
    validez = int(request.form.get('validez_dias', 7))
    fecha_str = request.form.get('fecha')
    productos_ids = request.form.getlist('producto_id[]')
    cantidades = request.form.getlist('cantidad[]')
    
    fecha_cot = datetime.strptime(fecha_str, '%Y-%m-%d') if fecha_str else datetime.utcnow()
    if not cliente_id or not productos_ids:
        return redirect(url_for('cotizaciones'))

    nueva_cot = Cotizacion(cliente_id=int(cliente_id), usuario_id=current_user.id, validez_dias=validez, total=0.0, fecha=fecha_cot, estado='Pendiente')
    db.session.add(nueva_cot)
    db.session.flush()

    total_cot = 0.0
    for p_id, cant in zip(productos_ids, cantidades):
        if p_id and cant:
            producto = Producto.query.get(int(p_id))
            cantidad = int(cant)
            if producto and cantidad > 0:
                subtotal = producto.precio * cantidad
                total_cot += subtotal
                db.session.add(DetalleCotizacion(cotizacion_id=nueva_cot.id, producto_id=producto.id, cantidad=cantidad, precio_unitario=producto.precio))

    nueva_cot.total = total_cot
    db.session.commit()
    return redirect(url_for('cotizaciones'))

@app.route('/cotizacion/<int:id>/editar', methods=['GET', 'POST'])
@login_required
def editar_cotizacion(id):
    cotizacion = Cotizacion.query.get_or_404(id)
    
    # Solo permitir editar si está pendiente
    if cotizacion.estado != 'Pendiente':
        flash('Solo se pueden editar cotizaciones en estado Pendiente.', 'error')
        return redirect(url_for('cotizaciones'))
        
    if request.method == 'POST':
        cliente_id = request.form.get('cliente_id')
        validez = int(request.form.get('validez_dias', 7))
        fecha_str = request.form.get('fecha')
        productos_ids = request.form.getlist('producto_id[]')
        cantidades = request.form.getlist('cantidad[]')
        
        if not cliente_id or not productos_ids:
            flash('Faltan datos obligatorios para actualizar la cotización.', 'error')
            return redirect(url_for('editar_cotizacion', id=id))
            
        cotizacion.cliente_id = int(cliente_id)
        cotizacion.validez_dias = validez
        if fecha_str:
            cotizacion.fecha = datetime.strptime(fecha_str, '%Y-%m-%d')
            
        # Limpiar detalles anteriores
        for d in cotizacion.detalles:
            db.session.delete(d)
            
        total_cot = 0.0
        for p_id, cant in zip(productos_ids, cantidades):
            if p_id and cant:
                producto = Producto.query.get(int(p_id))
                cantidad = int(cant)
                if producto and cantidad > 0:
                    subtotal = producto.precio * cantidad
                    total_cot += subtotal
                    db.session.add(DetalleCotizacion(
                        cotizacion_id=cotizacion.id, 
                        producto_id=producto.id, 
                        cantidad=cantidad, 
                        precio_unitario=producto.precio
                    ))
                    
        cotizacion.total = total_cot
        db.session.commit()
        flash('Cotización actualizada exitosamente.', 'success')
        return redirect(url_for('cotizaciones'))
        
    clientes = Cliente.query.all()
    productos = Producto.query.all()
    fecha_formato = cotizacion.fecha.strftime('%Y-%m-%d') if cotizacion.fecha else datetime.now().strftime('%Y-%m-%d')
    return render_template('editar_cotizacion.html', store_name="Himalaya Store SC", cotizacion=cotizacion, clientes=clientes, productos=productos, fecha_formato=fecha_formato)

@app.route('/cotizacion/<int:id>')
@login_required
def detalle_cotizacion(id):
    cotizacion = Cotizacion.query.get_or_404(id)
    billeteras = Billetera.query.all()
    return render_template('detalle_cotizacion.html', store_name="Himalaya Store SC", cotizacion=cotizacion, billeteras=billeteras)

@app.route('/cotizacion/<int:id>/convertir', methods=['POST'])
@login_required
def convertir_cotizacion(id):
    cotizacion = Cotizacion.query.get_or_404(id)
    billetera_id = request.form.get('billetera_id')
    
    if cotizacion.estado != 'Pendiente' or not billetera_id:
        return redirect(url_for('detalle_cotizacion', id=id))

    for d in cotizacion.detalles:
        if d.producto.stock_actual < d.cantidad:
            flash(f'Stock insuficiente para el producto: {d.producto.nombre}', 'error')
            return redirect(url_for('detalle_cotizacion', id=id))

    nueva_venta = Venta(
        cliente_id=cotizacion.cliente_id,
        billetera_id=int(billetera_id),
        usuario_id=current_user.id,
        total=cotizacion.total,
        fecha=datetime.utcnow()
    )
    db.session.add(nueva_venta)
    db.session.flush()

    for d in cotizacion.detalles:
        producto = d.producto
        producto.stock_actual -= d.cantidad
        db.session.add(DetalleVenta(
            venta_id=nueva_venta.id,
            producto_id=producto.id,
            cantidad=d.cantidad,
            precio_unitario=d.precio_unitario
        ))

    cotizacion.estado = 'Aprobada (Convertida a Venta)'
    db.session.commit()

    socketio.emit('notificacion_global', {
        'titulo': '🎯 Cotización Convertida en Venta',
        'mensaje': f'{current_user.username} convirtió la cotización #{cotizacion.id} en venta por ${cotizacion.total:.2f}.'
    })

    return redirect(url_for('ventas'))

@app.route('/cotizacion/<int:id>/eliminar', methods=['POST'])
@login_required
def eliminar_cotizacion(id):
    cot = Cotizacion.query.get_or_404(id)
    if cot.estado == 'Pendiente':
        db.session.delete(cot)
        db.session.commit()
    return redirect(url_for('cotizaciones'))

@app.route('/realizar_cierre', methods=['POST'])
@login_required
def realizar_cierre():
    anio = int(request.form.get('anio'))
    mes = int(request.form.get('mes'))
    
    ventas_mes = Venta.query.filter(
        db.extract('year', Venta.fecha) == anio,
        db.extract('month', Venta.fecha) == mes
    ).all()
    total_v = sum(v.total for v in ventas_mes)

    gastos_mes = Gasto.query.filter(
        db.extract('year', Gasto.fecha) == anio,
        db.extract('month', Gasto.fecha) == mes
    ).all()
    total_g = sum(g.monto for g in gastos_mes)

    nuevo_cierre = CierreMensual(
        anio=anio,
        mes=mes,
        usuario_id=current_user.id,
        total_ventas=total_v,
        total_gastos=total_g
    )
    db.session.add(nuevo_cierre)
    db.session.commit()

    flash(f'Cierre contable del periodo {mes}/{anio} generado exitosamente.', 'success')
    return redirect(url_for('cierres'))

@app.route('/cierre/<int:id>/reporte')
@login_required
def reporte_cierre(id):
    cierre = CierreMensual.query.get_or_404(id)
    
    ventas_mes = Venta.query.filter(
        db.extract('year', Venta.fecha) == cierre.anio,
        db.extract('month', Venta.fecha) == cierre.mes
    ).order_by(Venta.fecha.desc()).all()

    gastos_mes = Gasto.query.filter(
        db.extract('year', Gasto.fecha) == cierre.anio,
        db.extract('month', Gasto.fecha) == cierre.mes
    ).order_by(Gasto.fecha.desc()).all()

    billeteras = Billetera.query.all()

    meses_nombres = ["", "Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
    nombre_mes = meses_nombres[cierre.mes] if 1 <= cierre.mes <= 12 else str(cierre.mes)

    return render_template('reporte_cierre.html', 
                           store_name="Himalaya Store SC", 
                           cierre=cierre, 
                           nombre_mes=nombre_mes,
                           ventas=ventas_mes, 
                           gastos=gastos_mes,
                           billeteras=billeteras)   

@app.route('/cierre/<int:id>/eliminar', methods=['POST'])
@login_required
def eliminar_cierre(id):
    cierre = CierreMensual.query.get_or_404(id)
    db.session.delete(cierre)
    db.session.commit()
    flash('Cierre contable eliminado correctamente del historial.', 'success')
    return redirect(url_for('cierres'))

@app.route('/registrar_salida_inventario', methods=['POST'])
@login_required
def registrar_salida_inventario():
    concepto = request.form.get('concepto')
    observacion = request.form.get('observacion')
    fecha_str = request.form.get('fecha')
    
    productos_ids = request.form.getlist('producto_id[]')
    cantidades = request.form.getlist('cantidad[]')
    
    fecha = datetime.strptime(fecha_str, '%Y-%m-%d') if fecha_str else datetime.utcnow()
    
    nueva_salida = SalidaInventario(concepto=concepto, observacion=observacion, fecha=fecha)
    db.session.add(nueva_salida)
    db.session.flush()
    
    for p_id, cant in zip(productos_ids, cantidades):
        if p_id and cant:
            producto = Producto.query.get(int(p_id))
            cantidad_retirar = int(cant)
            
            if producto.stock_actual < cantidad_retirar:
                flash(f'Stock insuficiente para "{producto.nombre}". Stock actual: {producto.stock_actual}', 'error')
                db.session.rollback()
                return redirect(url_for('inventario'))
            
            producto.stock_actual -= cantidad_retirar
            detalle = DetalleSalida(salida_id=nueva_salida.id, producto_id=producto.id, cantidad=cantidad_retirar)
            db.session.add(detalle)
            
    db.session.commit()
    flash('Salida de inventario registrada y stock actualizado correctamente.', 'success')
    return redirect(url_for('inventario'))



if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    socketio.run(app, host='0.0.0.0', port=port, debug=False, allow_unsafe_werkzeug=True)