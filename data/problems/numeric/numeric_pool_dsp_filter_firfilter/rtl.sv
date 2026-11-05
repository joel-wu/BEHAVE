module fir2d_same_reflect_s16 (
    input logic clk,
    input logic rst,
    input logic in_valid,
    output logic in_ready,
    input logic [3:0] in_width_m1,
    input logic [3:0] in_height_m1,
    input logic [3:0] in_x,
    input logic [3:0] in_y,
    input logic [15:0] in_h00,
    input logic [15:0] in_h01,
    input logic [15:0] in_h02,
    input logic [15:0] in_h10,
    input logic [15:0] in_h11,
    input logic [15:0] in_h12,
    input logic [15:0] in_h20,
    input logic [15:0] in_h21,
    input logic [15:0] in_h22,
    output logic out_valid,
    input logic out_ready,
    output logic [31:0] out_y,
    output logic image_req_valid,
    output logic [7:0] image_req_addr,
    input logic image_req_ready,
    input logic image_resp_valid,
    input logic [15:0] image_resp_rdata
);
    localparam logic [1:0] IDLE=0, REQUEST=1, RESPONSE=2, OUTPUT=3;
    logic [1:0] state;
    logic [4:0] width, height, x, y;
    logic [3:0] tap;
    logic [143:0] kernel;
    logic [31:0] accumulator, result;
    logic signed [15:0] pixel, coefficient;
    logic signed [31:0] product;
    integer row_index, col_index;

    // A 3x3 stencil reaches at most one location beyond either image edge.
    function automatic integer reflect(input integer i, input integer size);
        if (i<0) reflect=-i-1;
        else if (i>=size) reflect=2*size-i-1;
        else reflect=i;
    endfunction

    assign row_index = reflect(int'(y)+1-int'(tap)/3, int'(height));
    assign col_index = reflect(int'(x)+1-int'(tap)%3, int'(width));
    assign image_req_addr = 8'(16*row_index+col_index);
    assign image_req_valid = state==REQUEST;
    assign pixel = image_resp_rdata;
    assign coefficient = kernel[16*tap +: 16];
    assign product = pixel*coefficient;
    assign in_ready = state==IDLE;
    assign out_valid = state==OUTPUT;
    assign out_y = result;

    always_ff @(posedge clk) begin
        if (rst) begin
            state<=IDLE;
            width<=1; height<=1; x<=0; y<=0;
            tap<=0; kernel<=0; accumulator<=0; result<=0;
        end else begin
            case(state)
                IDLE: if(in_valid) begin
                    width<={1'b0,in_width_m1}+5'd1;
                    height<={1'b0,in_height_m1}+5'd1;
                    x<={1'b0,in_x}; y<={1'b0,in_y};
                    kernel<={in_h22,in_h21,in_h20,in_h12,in_h11,in_h10,in_h02,in_h01,in_h00};
                    tap<=0; accumulator<=0;
                    if(in_x>in_width_m1 || in_y>in_height_m1) begin
                        result<=0; state<=OUTPUT;
                    end else state<=REQUEST;
                end
                REQUEST: if(image_req_ready) state<=RESPONSE;
                RESPONSE: if(image_resp_valid) begin
                    accumulator<=accumulator+product;
                    if(tap==8) begin
                        result<=accumulator+product;
                        state<=OUTPUT;
                    end else begin
                        tap<=tap+1'b1; state<=REQUEST;
                    end
                end
                OUTPUT: if(out_ready) state<=IDLE;
                default: state<=IDLE;
            endcase
        end
    end
endmodule
