"""Pruebas del motor de reglas (backend/reglas.py).

Los escenarios de la demo son los casos de prueba (seccion 12 de CLAUDE.md).
Se usa `unittest` de la biblioteca estandar: corre sin instalar nada con
    python3 -m unittest discover -s backend -v
y tambien lo levanta pytest si esta disponible.
"""

import unittest

from reglas import (
    Estado,
    clasificar_acceso,
    clasificar_carga_ups,
    clasificar_humedad,
    clasificar_humo,
    clasificar_temperatura,
    evaluar_lectura,
    hubo_flanco_critico,
    peor,
)

# Lectura base "todo normal"; cada prueba la altera solo en lo que le importa.
BASE = {"temperatura": 22.0, "humedad": 50.0, "carga_ups": 45.0}


def lectura(**cambios):
    """Evalua la lectura base con los cambios indicados."""
    return evaluar_lectura(**{**BASE, **cambios})


class TestEscenariosDemo(unittest.TestCase):
    """Los 5 botones del panel + los casos limite del enunciado."""

    def test_todo_normal(self):
        r = lectura()
        self.assertIs(r.estado_general, Estado.NORMAL)
        self.assertEqual(r.alertas, [])
        self.assertTrue(all(e is Estado.NORMAL for e in r.estados.values()))

    def test_temperatura_critica_alta(self):
        # Ola de calor: 35 C > 32.
        r = lectura(temperatura=35.0)
        self.assertIs(r.estados["temperatura"], Estado.CRITICO)
        self.assertIs(r.estado_general, Estado.CRITICO)
        self.assertEqual([a.variable for a in r.alertas], ["temperatura"])

    def test_temperatura_critica_baja(self):
        # 10 C < 15.
        r = lectura(temperatura=10.0)
        self.assertIs(r.estados["temperatura"], Estado.CRITICO)
        self.assertIs(r.estado_general, Estado.CRITICO)

    def test_humedad_critica(self):
        # 85% > 70 (condensacion).
        r = lectura(humedad=85.0)
        self.assertIs(r.estados["humedad"], Estado.CRITICO)
        self.assertIs(r.estado_general, Estado.CRITICO)

        # Y el otro extremo: 10% < 20 (electricidad estatica).
        r_seco = lectura(humedad=10.0)
        self.assertIs(r_seco.estados["humedad"], Estado.CRITICO)
        self.assertIs(r_seco.estado_general, Estado.CRITICO)

    def test_carga_ups_critica(self):
        # UPS sobrecargada: 95% > 90.
        r = lectura(carga_ups=95.0)
        self.assertIs(r.estados["carga_ups"], Estado.CRITICO)
        self.assertIs(r.estado_general, Estado.CRITICO)

    def test_humo_detectado(self):
        # Humo fuerza CRITICO aunque las 3 continuas esten perfectas.
        r = lectura(humo=True)
        self.assertIs(r.estados["humo"], Estado.CRITICO)
        self.assertIs(r.estado_general, Estado.CRITICO)
        self.assertIn("humo", [a.variable for a in r.alertas])

    def test_intrusion_puerta_abierta_sin_mantencion(self):
        r = lectura(puerta_abierta=True, mantencion=False)
        self.assertIs(r.estados["acceso"], Estado.CRITICO)
        self.assertIs(r.estado_general, Estado.CRITICO)
        self.assertFalse(r.acceso_autorizado)
        self.assertIn("acceso", [a.variable for a in r.alertas])

    def test_acceso_autorizado_puerta_abierta_con_mantencion(self):
        # Puerta abierta CON mantencion: acceso autorizado, no es critico.
        r = lectura(puerta_abierta=True, mantencion=True)
        self.assertIs(r.estados["acceso"], Estado.NORMAL)
        self.assertIs(r.estado_general, Estado.NORMAL)
        self.assertTrue(r.acceso_autorizado)
        self.assertEqual(r.alertas, [])

    def test_dos_advertencias_sin_critico(self):
        # Temperatura 30 (27-32) + carga UPS 80 (70-90): gana ADVERTENCIA.
        r = lectura(temperatura=30.0, carga_ups=80.0)
        self.assertIs(r.estados["temperatura"], Estado.ADVERTENCIA)
        self.assertIs(r.estados["carga_ups"], Estado.ADVERTENCIA)
        self.assertIs(r.estado_general, Estado.ADVERTENCIA)
        self.assertEqual(len(r.alertas), 2)
        self.assertTrue(all(a.severidad is Estado.ADVERTENCIA for a in r.alertas))


class TestGanaElPeor(unittest.TestCase):
    """La regla de decision: el estado general es el mas severo."""

    def test_peor_ordena_por_severidad(self):
        self.assertIs(peor(Estado.NORMAL, Estado.ADVERTENCIA), Estado.ADVERTENCIA)
        self.assertIs(peor(Estado.ADVERTENCIA, Estado.CRITICO), Estado.CRITICO)
        self.assertIs(peor(Estado.NORMAL, Estado.NORMAL), Estado.NORMAL)

    def test_un_critico_arrastra_a_todo_el_sistema(self):
        r = lectura(temperatura=30.0, humedad=65.0, carga_ups=95.0)
        self.assertIs(r.estado_general, Estado.CRITICO)

    def test_humo_manda_aunque_el_resto_este_normal(self):
        r = lectura(temperatura=22.0, humedad=50.0, carga_ups=10.0, humo=True)
        self.assertIs(r.estado_general, Estado.CRITICO)

    def test_intrusion_manda_aunque_el_resto_este_normal(self):
        r = lectura(puerta_abierta=True)
        self.assertIs(r.estado_general, Estado.CRITICO)


class TestUmbralesTemperatura(unittest.TestCase):
    def test_rangos(self):
        casos = [
            (14.9, Estado.CRITICO),
            (15.0, Estado.ADVERTENCIA),
            (17.9, Estado.ADVERTENCIA),
            (18.0, Estado.NORMAL),
            (27.0, Estado.NORMAL),
            (27.1, Estado.ADVERTENCIA),
            (32.0, Estado.ADVERTENCIA),
            (32.1, Estado.CRITICO),
        ]
        for valor, esperado in casos:
            with self.subTest(temperatura=valor):
                self.assertIs(clasificar_temperatura(valor), esperado)


class TestUmbralesHumedad(unittest.TestCase):
    def test_rangos(self):
        casos = [
            (19.9, Estado.CRITICO),
            (20.0, Estado.ADVERTENCIA),
            (39.9, Estado.ADVERTENCIA),
            (40.0, Estado.NORMAL),
            (60.0, Estado.NORMAL),
            (60.1, Estado.ADVERTENCIA),
            (70.0, Estado.ADVERTENCIA),
            (70.1, Estado.CRITICO),
        ]
        for valor, esperado in casos:
            with self.subTest(humedad=valor):
                self.assertIs(clasificar_humedad(valor), esperado)


class TestUmbralesCargaUps(unittest.TestCase):
    def test_rangos(self):
        casos = [
            (0.0, Estado.NORMAL),
            (69.9, Estado.NORMAL),
            (70.0, Estado.ADVERTENCIA),
            (90.0, Estado.ADVERTENCIA),
            (90.1, Estado.CRITICO),
        ]
        for valor, esperado in casos:
            with self.subTest(carga_ups=valor):
                self.assertIs(clasificar_carga_ups(valor), esperado)


class TestVariablesDeEvento(unittest.TestCase):
    def test_humo(self):
        self.assertIs(clasificar_humo(False), Estado.NORMAL)
        self.assertIs(clasificar_humo(True), Estado.CRITICO)

    def test_acceso_cuatro_combinaciones(self):
        self.assertIs(clasificar_acceso(False, False), Estado.NORMAL)
        self.assertIs(clasificar_acceso(False, True), Estado.NORMAL)
        self.assertIs(clasificar_acceso(True, True), Estado.NORMAL)
        self.assertIs(clasificar_acceso(True, False), Estado.CRITICO)


class TestFlancoCritico(unittest.TestCase):
    """Contar incidentes por flanco de subida, no por lectura."""

    def test_sube_a_critico(self):
        self.assertTrue(hubo_flanco_critico(Estado.NORMAL, Estado.CRITICO))
        self.assertTrue(hubo_flanco_critico(Estado.ADVERTENCIA, Estado.CRITICO))
        self.assertTrue(hubo_flanco_critico(None, Estado.CRITICO))

    def test_se_mantiene_critico_no_cuenta(self):
        self.assertFalse(hubo_flanco_critico(Estado.CRITICO, Estado.CRITICO))

    def test_baja_o_sigue_normal_no_cuenta(self):
        self.assertFalse(hubo_flanco_critico(Estado.CRITICO, Estado.NORMAL))
        self.assertFalse(hubo_flanco_critico(Estado.NORMAL, Estado.ADVERTENCIA))


if __name__ == "__main__":
    unittest.main(verbosity=2)
