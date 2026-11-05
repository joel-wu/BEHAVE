module rgb_color_space_hsv (
    input logic clk, init_reset, rst,
    input logic we,
    input logic [7:0] waddr,
    input logic [24:0] wdata,
    input logic valid_in,
    input logic [7:0] r_component, g_component, b_component,
    output logic [11:0] h_component,
    output logic [12:0] s_component,
    output logic [11:0] v_component,
    output logic valid_out
);
    function automatic [7:0] maximum(input [23:0] rgb);
        reg [7:0] a;
        begin a=rgb[23:16]>rgb[15:8] ? rgb[23:16] : rgb[15:8];
            maximum=a>rgb[7:0] ? a : rgb[7:0]; end
    endfunction
    function automatic [7:0] delta(input [23:0] rgb);
        reg [7:0] a;
        begin a=rgb[23:16]<rgb[15:8] ? rgb[23:16] : rgb[15:8];
            a=a<rgb[7:0] ? a : rgb[7:0]; delta=maximum(rgb)-a; end
    endfunction
    logic [24:0] reciprocal_table [0:255];
    logic [23:0] rgb_pipe [0:3];
    logic [36:0] results [0:3];
    logic [7:0] valid_pipe;
    logic [24:0] inverse_max, inverse_delta;
    logic [12:0] saturation;
    logic [7:0] r, g, b;
    logic signed [8:0] difference;
    logic signed [39:0] product_h, raw_h, remainder_h;
    logic [9:0] offset_h;
    logic [32:0] saturation_rounded;
    logic [11:0] hue;
    always_comb begin
        r=rgb_pipe[3][23:16]; g=rgb_pipe[3][15:8]; b=rgb_pipe[3][7:0];
        if(r>=g && r>=b) begin
            difference=$signed({1'b0,g})-$signed({1'b0,b}); offset_h=0;
        end else if(g>=b) begin
            difference=$signed({1'b0,b})-$signed({1'b0,r}); offset_h=480;
        end else begin
            difference=$signed({1'b0,r})-$signed({1'b0,g}); offset_h=960;
        end
        product_h=40'(difference)*40'sd60*$signed({1'b0,inverse_delta});
        raw_h=(product_h >>> 22)+$signed({1'b0,offset_h});
        remainder_h=raw_h % 40'sd1440;
        if(remainder_h<0) remainder_h=remainder_h+40'sd1440;
        hue=delta(rgb_pipe[3])==0 ? 12'd0 : remainder_h[11:0];
        saturation_rounded=33'(delta(rgb_pipe[2]))*33'(inverse_max)+33'd2048;
        valid_out=valid_pipe[7];
        h_component=valid_out ? results[3][36:25] : 12'd0;
        s_component=valid_out ? results[3][24:12] : 13'd0;
        v_component=valid_out ? results[3][11:0] : 12'd0;
    end
    always_ff @(posedge clk or posedge init_reset) begin
        if(init_reset) begin
            for(int i=0;i<256;i++) reciprocal_table[i]<=0;
        end else if(we) reciprocal_table[waddr]<=wdata;
    end
    always_ff @(posedge clk or posedge init_reset or posedge rst) begin
        if(init_reset || rst) begin
            valid_pipe<=0; inverse_max<=0; inverse_delta<=0; saturation<=0;
            for(int i=0;i<4;i++) begin rgb_pipe[i]<=0; results[i]<=0; end
        end else begin
            valid_pipe<={valid_pipe[6:0],valid_in};
            rgb_pipe[0]<={r_component,g_component,b_component};
            for(int i=1;i<4;i++) rgb_pipe[i]<=rgb_pipe[i-1];
            inverse_max<=reciprocal_table[maximum(rgb_pipe[1])];
            inverse_delta<=reciprocal_table[delta(rgb_pipe[2])];
            saturation<=maximum(rgb_pipe[2])==0 ? 13'd0 : saturation_rounded[24:12];
            results[0]<={hue,saturation,4'd0,maximum(rgb_pipe[3])};
            for(int i=1;i<4;i++) results[i]<=results[i-1];
        end
    end
endmodule
