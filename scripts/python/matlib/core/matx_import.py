"""Turn an online MaterialX record into a real library material.

Two paths, matching the two source KINDS:

* **package** - download the .mtlx + textures into <library>/matX/<name>/,
  then TRANSLATE the .mtlx into clean VOP nodes (core/matx_translate,
  built on Houdini's MaterialX Python API): fresh mtlximage /
  mtlxstandard_surface / ... nodes with real `file` inputs, flat in the
  builder, exactly like a hand-built material. (This replaced the old
  editmaterial LOP approach, which promoted every parameter and dropped
  the texture `file` inputs from the USD export - the black-material bug.)

* **values** - no download at all. Build an mtlxstandard_surface directly
  from the measured parameters (a "tier A preset" material).

Everything temporary lives in /obj or /stage and is destroyed in a
finally, so a failed import never leaves scene debris - the same
discipline as the thumbnail and import paths.
"""

from __future__ import annotations

import os

import hou

from matlib.core import debug, matx_sources, matx_translate
from matlib.helpers import helpers
from matlib.render import nodes as nodes_mod

#: Folder inside the library holding downloaded MaterialX sources. This
#: is PERMANENT, not staging: the imported network's mtlximage nodes
#: point at these texture files.
MATX_DIRNAME = "matX"

#: PhysicallyBased subsurfaceRadius is a mean-free-path DISTANCE in
#: CENTIMETRES (verified: their Milk [1.842,1.044,0.35] is the standard
#: skimmed-milk value [18.42,10.44,3.50] mm / 10), and Houdini's
#: mtlxstandard_surface subsurface_radius is likewise a distance (not a
#: 0-1 tint), multiplied by subsurface_scale. Houdini/USD scenes are
#: METRES by default, so subsurface_scale converts the cm radius to metres
#: (1 cm = 0.01 m). Nudge only if a scene's unit scale differs (e.g. 1.0
#: for a centimetre scene). Feeding the raw cm value with scale 1
#: scattered ~100x too far and washed dark materials out to white/yellow.
CM_TO_SCENE_UNITS = 0.01


def matx_dir(library_dir: str) -> str:
    return os.path.join(library_dir, MATX_DIRNAME)


def _credit_text(record, source) -> str:
    """The about/homage block for a downloaded material: source, author,
    and a link back to where it came from. Editable afterwards in the
    Material Info dialog."""
    lines = ['"%s" from the %s library.' % (record.title, record.source)]
    if record.author:
        lines.append("Created by %s." % record.author)
    url = ""
    try:
        url = source.page_url(record) or ""
    except Exception:
        url = ""
    if url:
        lines.append(url)
    lines.append("Please credit the creator as the license requires.")
    return "\n".join(lines)



def _values_to_standard_surface(values: dict, builder: hou.Node) -> hou.Node:
    """PhysicallyBased measured values -> mtlxstandard_surface constants.

    Only maps what the source actually measures; anything absent is left
    at the shader default rather than invented.

    Units, verified against the PhysicallyBased schema
    (github.com/AntonPalmqvist/physically-based-api) and the MtlX Standard
    Surface, so the physical values land at the right scale:

    * color / specularColor  linear rec709 RGB          -> as-is
    * metalness / roughness  0..1                        -> as-is
    * ior                    dielectric refractive index -> as-is
    * subsurfaceRadius       mean free path, CENTIMETRES -> x CM_TO_SCENE_UNITS
                             (via subsurface_scale; radius is a distance)
    * transmissionDepth      Beer-Lambert depth, METRES  -> as-is (OpenPBR
                             convention; scene default is metres)
    * transmissionDispersion Abbe number (Diamond 55.3)  -> as-is
    * thinFilmThickness      NANOMETRES (Pearl 420)       -> as-is
    * thinFilmIor            refractive index             -> as-is
    * complexIor             handled elsewhere: a material carrying n,k is
                             routed to _values_to_conductor_surface (a real
                             conductor BSDF) instead of this function"""
    shader = builder.createNode("mtlxstandard_surface")

    def _set(parm_name, value):
        if value is None:
            return
        try:
            if isinstance(value, (list, tuple)):
                pt = shader.parmTuple(parm_name)
                if pt is not None:
                    pt.set(tuple(float(v) for v in value)[: len(pt)])
            else:
                p = shader.parm(parm_name)
                if p is not None:
                    p.set(float(value))
        except hou.Error as exc:
            print("Amaze: could not set %s: %s" % (parm_name, exc))

    _set("base_color", values.get("color"))
    _set("metalness", values.get("metalness"))
    _set("specular_roughness", values.get("roughness"))
    _set("specular_IOR", values.get("ior"))
    _set("specular_color", values.get("specularColor"))
    # Transmission (Glass, Water, liquid Honey...). A transmissive
    # material tints the light passing THROUGH it by its own color -
    # Standard Surface's transmission_color is that tint, and it defaults
    # to white, so without this honey renders as clear as water. In this
    # dataset transmission is only ever 1, so any transmissive material
    # also gets its base color as the transmission tint.
    _set("transmission", values.get("transmission"))
    if values.get("transmission"):
        _set("transmission_color", values.get("color"))
    _set("transmission_depth", values.get("transmissionDepth"))
    _set("transmission_dispersion", values.get("transmissionDispersion"))
    # Subsurface scattering (crystallized Honey, Petroleum, Milk, Marble,
    # Skin...). These are NOT transmissive - the source gives a per-channel
    # subsurfaceRadius instead of transmission. subsurface_radius alone
    # does nothing until the subsurface WEIGHT is on and it has a color, so
    # enable it fully and tint it with the material's own color.
    #
    # mtlxstandard_surface's subsurface_radius is a 0..1 COLOR modulator
    # (default white=1), but PhysicallyBased gives raw physical
    # mean-free-paths (mm) that can exceed 1 - Petroleum's is [3, 1, 0.25].
    # A value >1 makes that channel scatter FURTHER than the object is
    # wide, so light passes almost straight through: a near-black material
    # washes out to a bright, warm (red-far) glow - the yellow Petroleum.
    # Normalise by the max so the per-channel TINT is preserved but nothing
    # exceeds the range the shader expects.
    radius = values.get("subsurfaceRadius")
    if radius:
        # subsurface_radius is a DISTANCE (mean free path), in the same
        # scene units as everything else - NOT a 0-1 tint. PhysicallyBased
        # gives it in centimetres, so subsurface_scale converts to the
        # scene's metres (see CM_TO_SCENE_UNITS). This is physically
        # grounded and keeps each material's own scattering distance
        # (petroleum scatters further than honey); SSS is still scene-scale
        # dependent, so on an unusually large/small object the user can
        # scale subsurface_scale on the shader.
        _set("subsurface", 1)
        _set("subsurface_color", values.get("color"))
        _set("subsurface_radius", radius)             # physical cm values
        _set("subsurface_scale", CM_TO_SCENE_UNITS)   # cm -> scene metres

    if debug.is_on():
        debug.event(
            "import", "preset values applied",
            base_color=values.get("color"),
            transmission=values.get("transmission"),
            subsurfaceRadius=values.get("subsurfaceRadius"),
            metalness=values.get("metalness"),
            roughness=values.get("roughness"),
        )
    # Thin film IS safe to copy here, unlike the Redshift converter:
    # there it was a non-zero DEFAULT sitting on every metal and painted
    # them iridescent. Here it is measured, and only on the two
    # materials that genuinely are thin-film - Pearl (420nm) and Soap
    # Bubble (500nm).
    _set("thin_film_thickness", values.get("thinFilmThickness"))
    _set("thin_film_IOR", values.get("thinFilmIor"))
    # complexIor is handled on a SEPARATE path (a conductor BSDF, see
    # _values_to_conductor_surface) - this function only builds the
    # dielectric/artistic-metal standard_surface case.
    return shader


def _values_to_conductor_surface(values: dict, builder: hou.Node) -> hou.Node:
    """A measured metal (PhysicallyBased complexIor) as a PHYSICALLY correct
    conductor rather than an artistic standard_surface metal.

    complexIor is [nR, kR, nG, kG, nB, kB] - the refractive index n and
    extinction k per channel. mtlxconductor_bsdf takes exactly those (`ior`
    = n, `extinction` = k) and computes the real complex-Fresnel
    reflectance, wired into an mtlxsurface terminal. This is the true metal
    response (e.g. gold's colour comes out of its n,k, not a painted
    swatch), which is why the 30 metals are routed here."""
    ci = values.get("complexIor") or []
    conductor = builder.createNode("mtlxconductor_bsdf")

    def _set_tuple(parm_name, value):
        try:
            pt = conductor.parmTuple(parm_name)
            if pt is not None:
                pt.set(tuple(float(v) for v in value)[: len(pt)])
        except hou.Error as exc:
            print("Amaze: could not set %s: %s" % (parm_name, exc))

    if len(ci) >= 6:
        _set_tuple("ior", (ci[0], ci[2], ci[4]))          # n per channel
        _set_tuple("extinction", (ci[1], ci[3], ci[5]))   # k per channel
    roughness = values.get("roughness")
    if roughness is not None:
        # conductor_bsdf roughness is a vector2 (anisotropic); the source
        # gives one scalar, so set both axes to it (isotropic).
        _set_tuple("roughness", (roughness, roughness))

    surface = builder.createNode("mtlxsurface")
    # conductor.out (BSDF) -> surface.bsdf; the funnel wires this mtlxsurface
    # into the builder's suboutput surface terminal.
    surface.setNamedInput("bsdf", conductor, 0)

    if debug.is_on():
        debug.event(
            "import", "conductor metal applied",
            name=values.get("name"),
            ior=(ci[0], ci[2], ci[4]) if len(ci) >= 6 else None,
            extinction=(ci[1], ci[3], ci[5]) if len(ci) >= 6 else None,
            roughness=roughness,
        )
    return surface




def import_record(record, source, resolution, library, preferences,
                  progress=None):
    """Import one online record into `library` (a MaterialLibrary).

    Returns (ok, reason). Never leaves scene debris on failure. progress
    (frac) is called with a 0..1 fraction during the download (package
    sources only - value sources have nothing to download)."""
    name = helpers.sanitize_usd_path(record.title) or "Material"
    debug.event("import", "start", title=record.title, name=name,
                source=record.source, kind=record.kind,
                category=record.category, resolution=resolution)
    scratch = hou.node("/obj").createNode("matnet")
    try:
        # Resolve the input FIRST (the part that can fail with I/O), then
        # let the shared Karma material engine own the container, wiring
        # and verification. The online importer is one ADAPTER: its
        # `produce` callback builds the shader network, nothing more.
        if record.kind == "values":
            values = source.fetch(record, None, None).get("values", {})

            def produce(builder):
                # Measured metals (complexIor n,k) go through a real
                # conductor BSDF; everything else is a standard_surface.
                if values.get("complexIor"):
                    return _values_to_conductor_surface(values, builder)
                return _values_to_standard_surface(values, builder)
        else:
            dest = os.path.join(matx_dir(preferences.dir), name)
            try:
                fetched = source.fetch(record, resolution, dest,
                                       progress=progress)
            except Exception as exc:
                debug.exception("download", exc, url=record.payload,
                                dest=dest)
                return (False, "Download failed: %s" % exc)
            mtlx_path = fetched.get("mtlx")
            if not mtlx_path or not os.path.exists(mtlx_path):
                return (False, "No .mtlx document in the downloaded package")
            repairs = matx_sources.repair_mtlx_references(mtlx_path, dest)
            if repairs:
                debug.event("import", "mtlx references repaired",
                            material=name, repairs=repairs)
                unresolved = [r for r in repairs if not r["fixed_to"]]
                if unresolved:
                    print(
                        "Amaze: %d texture(s) referenced by %s were not "
                        "downloaded and could not be matched - those inputs "
                        "will render black." % (len(unresolved), name)
                    )

            def produce(builder):
                return matx_translate.build_material(mtlx_path, builder, name)

        builder, shader = nodes_mod.build_karma_material(scratch, name, produce)

        if shader is None:
            return (False, "Could not build a shading network for " + name)

        library.add_asset(
            builder,
            record.category,
            ",".join(record.tags or []),
            False,
        )
        # add_asset() derives the renderer from the NODE, and a builder
        # full of mtlx* nodes reads as "Karma". These are their own
        # renderer, so override it on the asset just added.
        # Also credit the creators: about text (source, author, link) and
        # the license, shown/editable in the Material Info dialog.
        try:
            assets = library.assets
            if assets:
                assets[-1].renderer = "MtlX"
                assets[-1].about = _credit_text(record, source)
                assets[-1].license = record.licence or ""
                library.save()
        except Exception as exc:
            debug.exception("tag as MtlX", exc)
            print("Amaze: could not tag the import as MtlX: %s" % exc)
        debug.event("import", "registered", name=name,
                    rows=library.rowCount())
        return (True, "")
    finally:
        try:
            scratch.destroy()
        except (hou.OperationFailed, hou.ObjectWasDeleted):
            pass
