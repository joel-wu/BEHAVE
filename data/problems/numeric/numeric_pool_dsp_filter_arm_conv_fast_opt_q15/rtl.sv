module q15_fast_convolution_frame (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [15:0]  in_a0,
    input  logic [15:0]  in_a1,
    input  logic [15:0]  in_a2,
    input  logic [15:0]  in_a3,
    input  logic [15:0]  in_a4,
    input  logic [15:0]  in_a5,
    input  logic [15:0]  in_a6,
    input  logic [15:0]  in_a7,
    input  logic [15:0]  in_a8,
    input  logic [15:0]  in_a9,
    input  logic [15:0]  in_a10,
    input  logic [15:0]  in_a11,
    input  logic [15:0]  in_a12,
    input  logic [15:0]  in_a13,
    input  logic [15:0]  in_a14,
    input  logic [15:0]  in_a15,
    input  logic [15:0]  in_b0,
    input  logic [15:0]  in_b1,
    input  logic [15:0]  in_b2,
    input  logic [15:0]  in_b3,
    input  logic [15:0]  in_b4,
    input  logic [15:0]  in_b5,
    input  logic [15:0]  in_b6,
    input  logic [15:0]  in_b7,
    input  logic [15:0]  in_b8,
    input  logic [15:0]  in_b9,
    input  logic [15:0]  in_b10,
    input  logic [15:0]  in_b11,
    input  logic [15:0]  in_b12,
    input  logic [15:0]  in_b13,
    input  logic [15:0]  in_b14,
    input  logic [15:0]  in_b15,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [15:0]  out_data,
    output logic [4:0]   out_index
);

    logic busy;
    logic [4:0] out_ptr;
    logic [15:0] out_buffer [0:30];
    // Sample zero occupies the low 16 bits of each packed operand.
    wire [255:0] a = {in_a15, in_a14, in_a13, in_a12,
                     in_a11, in_a10, in_a9, in_a8,
                     in_a7, in_a6, in_a5, in_a4,
                     in_a3, in_a2, in_a1, in_a0};
    wire [255:0] b = {in_b15, in_b14, in_b13, in_b12,
                     in_b11, in_b10, in_b9, in_b8,
                     in_b7, in_b6, in_b5, in_b4,
                     in_b3, in_b2, in_b1, in_b0};

    function automatic logic [15:0] convolve(
        input logic [255:0] av,
        input logic [255:0] bv,
        input integer n
    );
        logic signed [31:0] acc, product, shifted;
        begin
            acc = 0;
            for (integer i=0; i<16; i=i+1) begin
                if (n-i >= 0 && n-i < 16) begin
                    product = $signed(av[16*i +: 16]) * $signed(bv[16*(n-i) +: 16]);
                    acc = acc + product;
                end
            end
            shifted = acc >>> 15;
            if (shifted > 32767) convolve = 16'h7fff;
            else if (shifted < -32768) convolve = 16'h8000;
            else convolve = shifted[15:0];
        end
    endfunction

    assign out_valid = busy;
    assign in_ready = !busy || (out_ready && out_ptr == 30);
    assign out_data = out_buffer[out_ptr];
    assign out_index = out_ptr;

    always_ff @(posedge clk) begin
        if (rst) begin
            busy <= 0;
            out_ptr <= 0;
            for (integer n=0; n<31; n=n+1) out_buffer[n] <= 0;
        end else if (in_valid && in_ready) begin
            for (integer n=0; n<31; n=n+1) out_buffer[n] <= convolve(a,b,n);
            out_ptr <= 0;
            busy <= 1;
        end else if (out_valid && out_ready) begin
            if (out_ptr == 30) begin
                out_ptr <= 0;
                busy <= 0;
            end else out_ptr <= out_ptr + 1'b1;
        end
    end
endmodule
