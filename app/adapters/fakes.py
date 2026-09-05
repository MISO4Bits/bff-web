"""Adaptadores en memoria. Permiten correr el BFF standalone y sirven de dobles en pruebas."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.domain import (
    ClienteCore,
    Conflicto,
    ConsentimientoVista,
    NoAutorizado,
    RecursoNoEncontrado,
    RegistroInput,
)


class FakeIdentityProvider:
    def __init__(self) -> None:
        # email -> (sub, password)
        self._por_email: dict[str, tuple[str, str]] = {}

    async def registrar(self, email: str, password: str) -> str:
        if email in self._por_email:
            raise Conflicto("El correo ya está registrado")
        sub = f"sub-{uuid.uuid4().hex[:12]}"
        self._por_email[email] = (sub, password)
        return sub

    async def autenticar(self, email: str, password: str) -> str:
        registro = self._por_email.get(email)
        if registro is None or registro[1] != password:
            raise NoAutorizado("credenciales inválidas")
        return registro[0]

    async def eliminar(self, sub: str) -> None:
        self._por_email = {
            e: v for e, v in self._por_email.items() if v[0] != sub
        }


class FakeCoreIdentity:
    def __init__(self) -> None:
        self._clientes: dict[str, ClienteCore] = {}
        self._por_identidad: dict[str, str] = {}
        self._por_documento: set[tuple[str, str]] = set()
        self._consentimientos: dict[tuple[str, str], ConsentimientoVista] = {}

    async def registrar_cliente(
        self, identity_ref: str, datos: RegistroInput
    ) -> ClienteCore:
        clave = (datos.tipo_documento, datos.numero_documento)
        if clave in self._por_documento:
            raise Conflicto("El documento ya está registrado")
        cliente = ClienteCore(
            id=str(uuid.uuid4()),
            primer_nombre=datos.primer_nombre,
            primer_apellido=datos.primer_apellido,
            email=datos.email,
            estado="ACTIVO",
            segundo_nombre=datos.segundo_nombre,
            segundo_apellido=datos.segundo_apellido,
            telefono=datos.telefono,
        )
        self._clientes[cliente.id] = cliente
        self._por_identidad[identity_ref] = cliente.id
        self._por_documento.add(clave)
        return cliente

    async def obtener_cliente(self, cliente_id: str) -> ClienteCore:
        cliente = self._clientes.get(cliente_id)
        if cliente is None:
            raise RecursoNoEncontrado("Cliente no encontrado")
        return cliente

    async def buscar_cliente_por_identidad(self, identity_ref: str) -> ClienteCore:
        cliente_id = self._por_identidad.get(identity_ref)
        if cliente_id is None:
            raise RecursoNoEncontrado("Cliente no encontrado")
        return self._clientes[cliente_id]

    async def listar_consentimientos(self, cliente_id: str) -> list[ConsentimientoVista]:
        await self.obtener_cliente(cliente_id)
        return [
            v for (cid, _s), v in sorted(self._consentimientos.items()) if cid == cliente_id
        ]

    async def otorgar_consentimiento(
        self, cliente_id: str, scope: str, politica_version: str, canal: str
    ) -> ConsentimientoVista:
        await self.obtener_cliente(cliente_id)
        vista = ConsentimientoVista(
            scope=scope,
            estado="OTORGADO",
            vigente=True,
            actualizado_en=datetime.now(timezone.utc),
        )
        self._consentimientos[(cliente_id, scope)] = vista
        return vista

    async def revocar_consentimiento(self, cliente_id: str, scope: str) -> None:
        await self.obtener_cliente(cliente_id)
        self._consentimientos[(cliente_id, scope)] = ConsentimientoVista(
            scope=scope,
            estado="REVOCADO",
            vigente=False,
            actualizado_en=datetime.now(timezone.utc),
        )
