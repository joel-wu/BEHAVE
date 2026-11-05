module vga_controller #(
    parameter int H_ACTIVE=640, parameter int H_FRONT=16,
    parameter int H_PULSE=96, parameter int H_BACK=48,
    parameter int V_ACTIVE=480, parameter int V_FRONT=10,
    parameter int V_PULSE=2, parameter int V_BACK=33
)(input logic clock_level, reset_level, input logic [7:0] color_in,
  output wire hsync, vsync, output wire [7:0] red, green, blue,
  output wire [9:0] next_x, next_y, output wire sync, clk, blank);
    localparam int H_TOTAL=H_ACTIVE+H_FRONT+H_PULSE+H_BACK;
    localparam int V_TOTAL=V_ACTIVE+V_FRONT+V_PULSE+V_BACK;
    logic [9:0] x=0, y=0;
    always_ff @(posedge clock_level or posedge reset_level) begin
        if(reset_level) begin x<=0; y<=0; end
        else if(x==H_TOTAL-1) begin
            x<=0;
            if(y==V_TOTAL-1) y<=0; else y<=y+1;
        end else x<=x+1;
    end
    wire active=(x<H_ACTIVE && y<V_ACTIVE && !reset_level);
    assign hsync=!(x>=H_ACTIVE+H_FRONT && x<H_ACTIVE+H_FRONT+H_PULSE);
    assign vsync=!(y>=V_ACTIVE+V_FRONT && y<V_ACTIVE+V_FRONT+V_PULSE);
    assign red=active ? {color_in[7:5],5'b0} : 8'b0;
    assign green=active ? {color_in[4:2],5'b0} : 8'b0;
    assign blue=active ? {color_in[1:0],6'b0} : 8'b0;
    assign next_x=active ? x : 10'b0;
    assign next_y=active ? y : 10'b0;
    assign sync=0;
    assign clk=clock_level;
    assign blank=!active;
endmodule
