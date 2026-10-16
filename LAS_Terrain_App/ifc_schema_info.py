"""
ifc_schema_info.py
Consulta los esquemas IFC (IfcOpenShell) para mostrar en la ventana de exportación qué clases y
tipos predefinidos existen en cada versión. Son las restricciones reales del estándar, no una lista a mano.
"""
import functools

import ifcopenshell.ifcopenshell_wrapper as w

SCHEMA_IDS = {"IFC2X3": "IFC2X3", "IFC4": "IFC4", "IFC4X3": "IFC4X3_ADD2"}


@functools.lru_cache(maxsize=None)
def _schema(version: str):
    return w.schema_by_name(SCHEMA_IDS[version])


def class_exists(version: str, ifc_class: str) -> bool:
    try:
        _schema(version).declaration_by_name(ifc_class)
        return True
    except Exception:
        return False


@functools.lru_cache(maxsize=None)
def predefined_types(version: str, ifc_class: str) -> tuple:
    """Valores del atributo PredefinedType de la clase en esa versión (vacío si no tiene)."""
    try:
        decl = _schema(version).declaration_by_name(ifc_class)
        for a in decl.all_attributes():
            if a.name() == "PredefinedType":
                t = a.type_of_attribute()
                en = t.declared_type() if hasattr(t, "declared_type") else t
                return tuple(en.enumeration_items())
    except Exception:
        pass
    return ()


def structure_note(version: str) -> str:
    """Cómo se organiza el modelo en cada versión (limitaciones del esquema)."""
    return {
        "IFC2X3": "IFC2X3: sin IfcBridge ni IfcBridgePart (se usan IfcBuilding e IfcElementAssembly), "
                  "sin IfcGeographicElement (se usa IfcBuildingElementProxy) y sin IfcMapConversion "
                  "(no se puede georreferenciar). Es la versión que lee Revit 2022.",
        "IFC4": "IFC4: sin IfcBridge (se usan IfcBuilding e IfcElementAssembly); con IfcGeographicElement "
                "e IfcMapConversion. Revit 2022 lo importa solo como referencia.",
        "IFC4X3": "IFC4X3: jerarquía IfcBridge > IfcBridgePart (UsageType obligatorio), con tipos como "
                  "GIRDER_SEGMENT. Revit 2022 no lo lee.",
    }[version]
