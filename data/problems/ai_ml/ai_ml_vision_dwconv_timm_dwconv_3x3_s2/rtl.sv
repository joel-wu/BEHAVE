module ai_ml_vision_dwconv_timm_dwconv_3x3_s2 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [383:0] in_x,
    input logic [215:0] in_weight,
    output logic out_valid,
    input logic out_ready,
    output logic [239:0] out_y
);
    logic busy;
    logic [3:0] lane;
    logic [383:0] pixels;
    logic [215:0] weights;
    integer oy, ox, channel, ky, kx, iy, ix;
    logic signed [7:0] pixel, coefficient;
    logic signed [31:0] total;

    assign in_ready = !rst && !busy && (!out_valid || out_ready);

    always_comb begin
        oy = lane / 6;
        ox = (lane / 3) % 2;
        channel = lane % 3;
        total = 0;
        pixel = 0;
        coefficient = 0;
        iy = 0;
        ix = 0;
        for (ky=0; ky<3; ky=ky+1) begin
            for (kx=0; kx<3; kx=kx+1) begin
                iy = 2*oy + ky - 1;
                ix = 2*ox + kx - 1;
                if (iy>=0 && iy<4 && ix>=0 && ix<4) begin
                    pixel = $signed(pixels[8*((iy*4+ix)*3+channel) +: 8]);
                    coefficient = $signed(weights[8*((channel*3+ky)*3+kx) +: 8]);
                    total = total + pixel * coefficient;
                end
            end
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 0;
            lane <= 0;
            pixels <= 0;
            weights <= 0;
            out_valid <= 0;
            out_y <= 0;
        end else begin
            if (out_valid && out_ready) out_valid <= 0;
            if (in_valid && in_ready) begin
                pixels <= in_x;
                weights <= in_weight;
                lane <= 0;
                busy <= 1;
            end else if (busy) begin
                out_y[20*lane +: 20] <= total[19:0];
                if (lane==11) begin
                    busy <= 0;
                    out_valid <= 1;
                    lane <= 0;
                end else lane <= lane+1;
            end
        end
    end
endmodule
