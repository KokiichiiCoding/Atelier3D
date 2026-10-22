"""ComfyUI API graphs for every Atelier3D job, built in code.

The image-to-3D graph follows Comfy-Org's official ``3d_pixal3d_trellis2_image_to_model``
template (ComfyUI 0.42): BiRefNet cut-out, MoGe field of view, Pixal3D / TRELLIS.2 structure,
shape, upsample and texture stages, then remesh, decimate, UV unwrap and PBR bakes.
Each node carries a stage label so progress can be reported per stage, and output nodes are
named so the pipeline knows which saved file is which.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# Per-quality sampler steps and resolutions. "standard" is the template's defaults except the
# upsample target (1536 in the template), which costs most of the VRAM; "high" restores it.
QUALITY: dict[str, dict[str, int]] = {
    "draft":    dict(ss_steps=8,  shape_steps=12, refine_steps=8,  tex_steps=8,  upsample=1024, remesh=512),
    "standard": dict(ss_steps=12, shape_steps=20, refine_steps=12, tex_steps=12, upsample=1024, remesh=768),
    "high":     dict(ss_steps=12, shape_steps=20, refine_steps=12, tex_steps=12, upsample=1536, remesh=768),
}
TEXTURE_SIZES = (1024, 2048, 4096)
FACE_LIMITS = (5_000, 1_000_000)
ENGINES = {"pixal3d": "pixal3d_int8_convrot.safetensors",
           "trellis2": "trellis_2_int8_convrot.safetensors"}
MULTIVIEW_UNET = "pixal3d_multiview_int8_convrot.safetensors"
VIEWS = ("front", "left", "back", "right")

STAGES_3D = [  # id, label, share of the total time (measured shares replace these later)
    ("prepare", "Preparing image", 0.08),
    ("structure", "Blocking out", 0.12),
    ("shape", "Sculpting", 0.26),
    ("refine", "Refining detail", 0.18),
    ("texture", "Painting", 0.14),
    ("bake", "Baking PBR maps", 0.22),
]
STAGES_CONCEPTS = [("load", "Loading model", 0.25), ("draw", "Drawing concepts", 0.75)]


@dataclass
class Graph:
    nodes: dict[str, dict] = field(default_factory=dict)
    stage_of: dict[str, str] = field(default_factory=dict)
    outputs: dict[str, str] = field(default_factory=dict)   # node id -> artifact name

    def add(self, class_type: str, stage: str, out: str | None = None, **inputs: Any) -> "Ref":
        nid = str(len(self.nodes) + 1)
        self.nodes[nid] = {"class_type": class_type, "inputs": inputs}
        self.stage_of[nid] = stage
        if out:
            self.outputs[nid] = out
        return Ref(nid)

    def prompt(self) -> dict:
        return self.nodes


@dataclass(frozen=True)
class Ref:
    id: str

    def __getitem__(self, slot: int) -> list:
        return [self.id, slot]


def check_params(quality: str, faces: int, texture: int) -> None:
    if quality not in QUALITY:
        raise ValueError(f"quality must be one of {', '.join(QUALITY)}")
    if not FACE_LIMITS[0] <= faces <= FACE_LIMITS[1]:
        raise ValueError(f"faces must be between {FACE_LIMITS[0]} and {FACE_LIMITS[1]}")
    if texture not in TEXTURE_SIZES:
        raise ValueError(f"texture must be one of {TEXTURE_SIZES}")


def _cutout(g: Graph, image_name: str, bg_model: Ref, tag: str = "") -> Ref:
    """Load an image, cut the subject out and frame it in a 1024 square (black background)."""
    img = g.add("LoadImage", "prepare", image=image_name)
    mask = g.add("RemoveBackground", "prepare", bg_removal_model=bg_model[0], image=img[0])
    crop = g.add("ImageCropToMask", "prepare", images=img[0], masks=mask[0], width=1024,
                 height=1024, pad_factor=1.1, grow_mask=0, background="#000000")
    g.add("SaveImage", "prepare", out=f"input{tag}", images=crop[0], filename_prefix="atelier/input")
    return crop


def image_to_3d(image_name: str | None = None, *, views: dict[str, str] | None = None,
                seed: int, quality: str = "standard", faces: int = 150_000,
                texture: int = 2048, engine: str = "pixal3d") -> Graph:
    """Image (or front/left/back/right views) to a UV-unwrapped mesh with PBR maps."""
    check_params(quality, faces, texture)
    if (image_name is None) == (views is None):
        raise ValueError("pass either image_name or views")
    if views is not None and (not views or set(views) - set(VIEWS)):
        raise ValueError(f"views must be a non-empty subset of {VIEWS}")
    if engine not in ENGINES:
        raise ValueError(f"engine must be one of {', '.join(ENGINES)}")
    q = QUALITY[quality]
    g = Graph()

    bg = g.add("LoadBackgroundRemovalModel", "prepare", bg_removal_name="birefnet.safetensors")
    dino = g.add("CLIPVisionLoader", "prepare", clip_name="dino_v3_L_naf_fp32.safetensors")
    if views is not None:
        crops = {v: _cutout(g, name, bg, f"_{v}") for v, name in views.items()}
        # 20 degrees is the rig the multi-view model was trained on (turnaround sheets, renders)
        cond = g.add("Pixal3DMultiViewConditioning", "prepare", clip_vision_model=dino[0], fov=20.0,
                     **{v: c[0] for v, c in crops.items()})
        unet_name = MULTIVIEW_UNET
    else:
        crop = _cutout(g, image_name, bg)
        if engine == "pixal3d":
            moge = g.add("LoadMoGeModel", "prepare", model_name="moge_2_vitl_normal_fp16.safetensors")
            geo = g.add("MoGeInference", "prepare", moge_model=moge[0], image=crop[0],
                        resolution_level=9, fov_x_degrees=0.0, batch_size=4,
                        force_projection=True, apply_mask=True, refine_steps=3)
            fov = g.add("MoGeGeometryToFOV", "prepare", moge_geometry=geo[0], axis="horizontal",
                        unit="degrees")
            cond = g.add("Pixal3DConditioning", "prepare", clip_vision_model=dino[0],
                         image=crop[0], camera_angle_x=fov[0])
        else:
            cond = g.add("Trellis2Conditioning", "prepare", clip_vision_model=dino[0], image=crop[0])
        unet_name = ENGINES[engine]

    unet = g.add("UNETLoader", "structure", unet_name=unet_name, weight_dtype="default")
    shape_vae = g.add("VAELoader", "structure", vae_name="trellis_2_shape_vae_bf16.safetensors")
    # CFG schedules match the original Pixal3D / TRELLIS.2 pipeline defaults
    m = g.add("CFGOverride", "structure", model=unet[0], cfg=1.0, start_percent=0.667, end_percent=1.0)
    m = g.add("RescaleCFG", "structure", model=m[0], multiplier=0.7)
    m_struct = g.add("ModelSamplingSD3", "structure", model=m[0], shift=5.0)
    m = g.add("CFGOverride", "shape", model=unet[0], cfg=1.0, start_percent=0.769, end_percent=1.0)
    m_shape = g.add("RescaleCFG", "shape", model=m[0], multiplier=0.5)

    empty = g.add("EmptyTrellis2LatentStructure", "structure", batch_size=1)
    s = g.add("KSampler", "structure", model=m_struct[0], seed=seed, steps=q["ss_steps"], cfg=7.5,
              sampler_name="euler", scheduler="normal", positive=cond[0], negative=cond[1],
              latent_image=empty[0], denoise=1.0)
    voxel = g.add("VaeDecodeStructureTrellis2", "structure", samples=s[0], vae=shape_vae[0],
                  resolution="32")
    block = g.add("VoxelToMesh", "structure", voxel=voxel[0], algorithm="basic", threshold=0.6)
    g.add("SaveGLB", "structure", out="preview_structure", mesh=block[0],
          filename_prefix="atelier/structure")

    st = g.add("Trellis2ShapeStage", "shape", positive=cond[0], negative=cond[1], voxel=voxel[0])
    shape = g.add("KSampler", "shape", model=m_shape[0], seed=seed + 1, steps=q["shape_steps"],
                  cfg=7.5, sampler_name="euler", scheduler="normal", positive=st[0],
                  negative=st[1], latent_image=st[2], denoise=1.0)
    up = g.add("Trellis2UpsampleStage", "refine", positive=st[0], negative=st[1],
               shape_latent=shape[0], vae=shape_vae[0], target_resolution=q["upsample"])
    fine = g.add("KSampler", "refine", model=m_shape[0], seed=seed + 1, steps=q["refine_steps"],
                 cfg=7.5, sampler_name="euler", scheduler="simple", positive=up[0],
                 negative=up[1], latent_image=up[2], denoise=1.0)
    hi = g.add("VaeDecodeShapeTrellis", "refine", samples=fine[0], vae=shape_vae[0])

    tex_vae = g.add("VAELoader", "texture", vae_name="trellis_2_texture_vae_bf16.safetensors")
    ts = g.add("Trellis2TextureStage", "texture", positive=up[0], negative=up[1], shape_latent=fine[0])
    tex = g.add("KSampler", "texture", model=unet[0], seed=seed + 2, steps=q["tex_steps"], cfg=1.0,
                sampler_name="euler", scheduler="normal", positive=ts[0], negative=ts[1],
                latent_image=ts[2], denoise=1.0)
    colors = g.add("VaeDecodeTextureTrellis", "texture", samples=tex[0], vae=tex_vae[0],
                   shape_subdivides=hi[1])

    remeshed = g.add("RemeshMesh", "bake", mesh=hi[0], resolution=q["remesh"], sign_mode="solid",
                     **{"sign_mode.fill": 20.0, "sign_mode.qef": False}, band=1.0, project_back=0.0,
                     fix_poles=False, smooth_iters=20, drop_small_components=0.01,
                     precluster_max_verts=20_000_000)
    low = g.add("DecimateMesh", "bake", mesh=remeshed[0], target_face_count=faces,
                placement_mode="midpoint")
    low = g.add("MeshSmoothNormals", "bake", mesh=low[0], crease_angle=180.0)
    painted = g.add("PaintMesh", "bake", mesh=low[0], voxel_colors=colors[0])
    g.add("SaveGLB", "bake", out="preview_painted", mesh=painted[0], filename_prefix="atelier/painted")

    uv = g.add("UnwrapMesh", "bake", mesh=low[0], segmenter="pec", resolution=texture, padding=1,
               weld_distance=0.0002)
    maps = g.add("BakeTextureFromVoxel", "bake", mesh=uv[0], voxel_colors=colors[0],
                 texture_size=texture, reference_mesh=hi[0])
    normal = g.add("BakeNormalMapFromMesh", "bake", low_poly=uv[0], high_poly=remeshed[0],
                   resolution=min(texture, 2048), cage_distance=0.05, ignore_backfaces=True)
    ao = g.add("BakeAmbientOcclusion", "bake", low_poly=uv[0], high_poly=remeshed[0],
               resolution=min(texture, 2048) // 2, samples=64, max_distance=0.71, strength=1.0,
               bias=0.01)
    final = g.add("ApplyTextureToMesh", "bake", mesh=uv[0], base_color=maps[0], metallic=maps[1],
                  roughness=maps[2], occlusion=ao[0], normal_map=normal[0])
    final = g.add("MeshSmoothNormals", "bake", mesh=final[0], crease_angle=180.0)
    g.add("SaveGLB", "bake", out="model", mesh=final[0], filename_prefix="atelier/model")
    for name, ref in (("map_base_color", maps[0]), ("map_metallic", maps[1]),
                      ("map_roughness", maps[2]), ("map_normal", normal[0]), ("map_ao", ao[0])):
        g.add("SaveImage", "bake", out=name, images=ref, filename_prefix=f"atelier/{name}")
    return g


# ---------------------------------------------------------------- text to image

NETA_SYSTEM = "You are an assistant designed to generate high quality anime images based on textual prompts. <Prompt Start> "
NETA_NEG_SYSTEM = "You are an assistant designed to generate low-quality images based on textual prompts <Prompt Start> "


def concepts(model: str, prompt: str, negative: str, *, seed: int, count: int = 4,
             width: int = 1024, height: int = 1024) -> Graph:
    """Concept images for Text to 3D. ``model`` is an id from ``models.text_models()``."""
    if not 1 <= count <= 4:
        raise ValueError("count must be 1-4")
    g = Graph()
    if model == "zimage":  # official z_image_turbo_int8 template
        unet = g.add("UNETLoader", "load", unet_name="z_image_turbo_int8_convrot.safetensors",
                     weight_dtype="default")
        clip = g.add("CLIPLoader", "load", clip_name="qwen_3_4b_fp8_mixed.safetensors",
                     type="lumina2", device="default")
        vae = g.add("VAELoader", "load", vae_name="ae.safetensors")[0]
        m = g.add("ModelSamplingAuraFlow", "load", model=unet[0], shift=3.0)
        pos = g.add("CLIPTextEncode", "load", text=prompt, clip=clip[0])
        neg = g.add("ConditioningZeroOut", "load", conditioning=pos[0])
        latent = g.add("EmptySD3LatentImage", "load", width=width, height=height, batch_size=count)
        sampler = dict(steps=8, cfg=1.0, sampler_name="res_multistep", scheduler="simple")
    elif model == "netayume":  # official NetaYume Lumina template
        ck = g.add("CheckpointLoaderSimple", "load", ckpt_name="NetaYumev35_pretrained_all_in_one.safetensors")
        m = g.add("ModelSamplingAuraFlow", "load", model=ck[0], shift=4.0)
        vae = ck[2]
        pos = g.add("CLIPTextEncode", "load", text=NETA_SYSTEM + prompt, clip=ck[1])
        neg = g.add("CLIPTextEncode", "load", text=NETA_NEG_SYSTEM + negative, clip=ck[1])
        latent = g.add("EmptySD3LatentImage", "load", width=width, height=height, batch_size=count)
        sampler = dict(steps=30, cfg=4.0, sampler_name="res_multistep", scheduler="simple")
    elif model.startswith("sdxl:"):
        ck = g.add("CheckpointLoaderSimple", "load", ckpt_name=model[5:])
        m = ck
        vae = ck[2]
        pos = g.add("CLIPTextEncode", "load", text=prompt, clip=ck[1])
        neg = g.add("CLIPTextEncode", "load", text=negative, clip=ck[1])
        latent = g.add("EmptyLatentImage", "load", width=width, height=height, batch_size=count)
        sampler = dict(steps=28, cfg=5.5, sampler_name="euler_ancestral", scheduler="normal")
    else:
        raise ValueError(f"unknown text model {model!r}")
    s = g.add("KSampler", "draw", model=m[0], seed=seed, positive=pos[0], negative=neg[0],
              latent_image=latent[0], denoise=1.0, **sampler)
    img = g.add("VAEDecode", "draw", samples=s[0], vae=vae)
    g.add("SaveImage", "draw", out="concept", images=img[0], filename_prefix="atelier/concept")
    return g


# Prompt shaping: image-to-3D works best from one centred subject on a plain background,
# lit evenly, with nothing cropped. Style words steer the look of the final texture.
STYLES = {
    "stylized": "stylized 3D game asset, clean shapes, hand-painted colors",
    "realistic": "photorealistic product render, physically based materials",
    "anime": "anime style figure, clean cel-shaded colors",
    "toy": "vinyl toy figurine, smooth plastic, soft colors",
    "lowpoly": "low poly 3D model, flat colors, faceted",
    "sculpture": "marble and bronze sculpture, museum piece",
}
FRAMING = ("single subject, centered, whole object fully in frame, plain white background, "
           "soft even studio lighting, no cast shadows, sharp focus, high detail")
CHARACTER = "full body, standing in a relaxed A-pose, arms slightly away from the body, front view"
NEGATIVE = ("multiple objects, cropped, cut off, out of frame, scenery, busy background, text, "
            "watermark, logo, signature, harsh shadows, blurry, low quality, deformed")


def shape_prompt(prompt: str, style: str = "stylized", character: bool = False) -> tuple[str, str]:
    if style not in STYLES:
        raise ValueError(f"style must be one of {', '.join(STYLES)}")
    parts = [prompt.strip().rstrip("."), STYLES[style]]
    if character:
        parts.append(CHARACTER)
    parts.append(FRAMING)
    return ", ".join(parts), NEGATIVE
