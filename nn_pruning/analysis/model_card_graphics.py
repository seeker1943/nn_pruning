import plotly.graph_objects as go
from io import StringIO

import plotly.io as pio
import plotly.express as px
pio.orca.config.use_xvfb = True

import bokeh.plotting
import bokeh.models
from bokeh.resources import CDN
from bokeh.embed import autoload_static

from pathlib import Path
import torch
from .graph_util import BokehHelper

class PruningInfoBokehPlotter(BokehHelper):


    def create_fig(self, layer_count, pruned_heads, heads_count):
        from bokeh.plotting import figure
        import copy

        layers = list([str(x) for x in range(layer_count)])

        kinds = ["active", "pruned"]
        colors = ["#0000ff", "#ffcccc"]

        active_by_layer = []
        pruned_by_layer = []
        for i in range(layer_count):
            pruned = len(pruned_heads.get(str(i), []))
            active = heads_count - pruned
            pruned_by_layer.append(pruned)
            active_by_layer.append(active)

        data = {'layers': layers,
                'pruned': pruned_by_layer,
                'active': active_by_layer, }

        p = figure(x_range=layers, plot_height=400, title="Pruned Transformer Heads",
                   toolbar_location=None, tools="",
                   x_axis_label="Layer index",
                   y_axis_label="Heads count")

        rs = p.vbar_stack(kinds, x='layers', width=0.9, color=colors, source=data,
                          legend_label=kinds)

        p.y_range.start = 0
        p.x_range.range_padding = 0.1
        p.xgrid.grid_line_color = None
        p.axis.minor_tick_line_color = None
        p.outline_line_color = None
        p.legend.location = None
        # p.legend.orientation = "horizontal"

        # rs.reverse()
        # kinds.reverse()

        legend = bokeh.models.Legend(items=[(kind, [r]) for (kind, r) in zip(kinds, rs)],
                                     location=(10, 0), orientation="horizontal")
        p.add_layout(legend, 'above')

        return p


class DensityPlotter(BokehHelper):
    def __init__(self,
                 model,
                 dest_path,
                 url_base,
                 js_path,
                 width=505,
                 height=300,
                 div_id="density",
                 block_size=(32, 32),
                 full_color=[0, 0, 255],
                 empty_color=[255, 190, 190]):
        super().__init__(div_id=div_id, js_path = js_path)
        self.model = model
        self.dest_path = Path(dest_path)
        self.width = width
        self.height = height
        if not self.dest_path.exists():
            self.dest_path.mkdir()
        self.block_size = block_size
        self.full_color = full_color
        self.empty_color = empty_color
        self.url_base = url_base

    def matrix_preprocess(self, matrix):
        self.block_size = (32, 32)
        block_size = self.block_size
        shape = matrix.shape
        matrix = matrix.reshape(shape[0] // block_size[0], block_size[0], shape[1] // block_size[1], block_size[1])
        matrix = matrix != 0
        matrix = matrix.any(1).any(2).int()
        return matrix

    def color_to_tensor(self, color):
        return torch.tensor(color).float().unsqueeze(0)

    def colorize_matrix(self, matrix):
        import torch
        full_color_tensor = self.color_to_tensor(self.full_color)
        empty_color_tensor = self.color_to_tensor(self.empty_color)

        m = matrix.float()
        m0 = m.unsqueeze(-1).matmul(full_color_tensor)
        m1 = (1 - m).unsqueeze(-1).matmul(empty_color_tensor)
        m_final = m0 + m1

        return m_final

    def create_image(self, matrix, file_path):
        ratio = 8
        fig = px.imshow(matrix)
        fig.update_layout(width = int(matrix.shape[1] * ratio), height = int(matrix.shape[0] * ratio), margin=dict(l=2, r=2, b=2, t=2))
        fig.write_image(file_path)

    def replacements_apply(self, s, replacements):
        for r in replacements:
            if isinstance(r, str):
                s = s.replace(r, "")
            else:
                s = s.replace(*r)
        return s

    def process_matrix(self, name, matrix):
        if not name.startswith("bert.encoder.layer"):
            return
        if matrix.dim() < 2:
            return

        parts = name.split(".")
        density = float((matrix != 0).sum() / matrix.numel())
        block_matrix = self.matrix_preprocess(matrix)
        color_matrix = self.colorize_matrix(block_matrix)

        filename = self.replacements_apply(name, ["bert.encoder.", (".", "_"), ("_weight", ".png"), ])

        layer = dict(name=name, filename=filename, density=density, size=matrix.shape)
        self.layers.append(layer)
        self.create_image(color_matrix, (self.dest_path / filename).open("wb"))

        return matrix

    def process_matrices(self):
        self.layers = []

        for name, parameter in self.model.named_parameters():
            self.process_matrix(name, parameter)

    def add_info(self, traces_, kind, **kwargs):
        if kind not in traces_:
            traces_[kind] = {}
            for k in kwargs:
                traces_[kind][k] = []
        d = traces_[kind]

        for k, v in kwargs.items():
            d[k].append(v)

    def layer_short_name(self, name):
        shortname = name
        shortname = shortname.split(".")
        shortname = shortname[3:]
        shortname = ".".join(shortname)
        shortname = self.replacements_apply(shortname, [".self", ".weight", ".dense", (".", ".")])

        return shortname

    def plot(self):
        self.process_matrices()
        traces = {}
        part_index = 0
        positions = []
        for layer in self.layers:
            name = layer["name"]
            density = layer["density"]
            height = density

            if "attention.self.query" in name:
                increment = 1
                kind = 'query'
            elif "attention.self.key" in name:
                increment = 1
                kind = "key"
            elif "attention.self.value" in name:
                increment = 1
                kind = "value"
            elif "attention.output.dense.weight" in name:
                kind = "fully connected"
                increment = 1
            elif "attention" not in name:
                increment = 1
                kind = "fully connected"
            else:
                print(name)
                assert (False)

            shortname = self.layer_short_name(name)

            x = part_index / 6 + 1 / 12
            url = f"{self.url_base}/{layer['filename']}"
            img_height = str(int(layer["size"][0] / 8)) + "px"
            img_width = str(int(layer["size"][1] / 8)) + "px"
            self.add_info(traces,
                          kind,
                          x=x,
                          height=height,
                          name=shortname,
                          density=f"{density:0.2f}",
                          url=url,
                          img_height=img_height,
                          img_width=img_width)
            if x not in positions:
                positions.append(x)

            part_index += increment

        colors = ["#6573f7", "#ed5642", "#20cb97", "#aa69f7"]

        hover = bokeh.models.HoverTool(
            tooltips="""
        <div>
            <div style="margin-bottom:10px">
                <span style="font-size: 15px;"><b>@name</b><br/>density=@density</span>
            </div>
            <div>            
                <img
                    src="@url" height="@img_height" width="@img_width" alt="@url"
                    style="float: left; margin: 0px 15px 15px 0px;"
                    border="0"
                />
            </div>
        </div>
        """)

        self.fig = bokeh.plotting.figure(plot_height=self.height,
                                         plot_width=self.width,
                                         title="Transformer Layers",
                                         tools=[hover])

        p = self.fig
        width = 1 / 8

        for i, key in enumerate(traces):
            p.vbar(top="height",
                   x="x",
                   width=width,
                   color=colors[i],
                   source=traces[key],
                   legend_label=key,
                   name=key)

        p.y_range.start = 0
        p.x_range.range_padding = 0.1
        p.xgrid.grid_line_color = None
        p.axis.minor_tick_line_color = None
        p.outline_line_color = None
        p.legend.location = "top_left"
        p.legend.orientation = "horizontal"

        p.xaxis.axis_label = "Layer"
        p.yaxis.axis_label = "Density"

