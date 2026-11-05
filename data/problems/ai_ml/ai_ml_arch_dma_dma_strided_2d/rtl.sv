module ai_ml_arch_dma_dma_strided_2d (
    input logic clk, rst, in_valid,
    output logic in_ready,
    input logic [63:0] in_source_address,
    input logic [63:0] in_destination_address,
    input logic [31:0] in_line_size,
    input logic [13:0] in_line_repeat,
    input logic [13:0] in_surface_repeat,
    input logic [31:0] in_source_line,
    input logic [31:0] in_destination_line,
    input logic [31:0] in_source_surface,
    input logic [31:0] in_destination_surface,
    input logic [0:0] in_source_type,
    input logic [0:0] in_destination_type,
    input logic out_ready,
    output logic out_valid,
    output logic [63:0] out_source_address,
    output logic [63:0] out_destination_address,
    output logic [26:0] out_line,
    output logic [0:0] out_source_type,
    output logic [0:0] out_destination_type,
    output logic [12:0] out_line_repeat,
    output logic [31:0] out_source_line,
    output logic [31:0] out_destination_line,
    output logic [12:0] out_surface_repeat,
    output logic [31:0] out_source_surface,
    output logic [31:0] out_destination_surface,
    output logic [0:0] out_enable,
    output logic [0:0] out_illegal
);
    logic bad;
    logic [63:0] next_out_source_address;
    logic [63:0] next_out_destination_address;
    logic [26:0] next_out_line;
    logic [0:0] next_out_source_type;
    logic [0:0] next_out_destination_type;
    logic [12:0] next_out_line_repeat;
    logic [31:0] next_out_source_line;
    logic [31:0] next_out_destination_line;
    logic [12:0] next_out_surface_repeat;
    logic [31:0] next_out_source_surface;
    logic [31:0] next_out_destination_surface;
    logic [0:0] next_out_enable;
    logic [0:0] next_out_illegal;

    always_comb begin
        bad = in_line_repeat == 0 || in_line_repeat > 8192 || in_surface_repeat == 0 || in_surface_repeat > 8192;
        bad = bad || in_line_size == 0 || in_line_size[4:0] != 0;
        bad = bad || in_source_line < in_line_size || in_destination_line < in_line_size;
        bad = bad || {14'd0,in_source_surface} < {14'd0,in_source_line} * {32'd0,in_line_repeat};
        bad = bad || {14'd0,in_destination_surface} < {14'd0,in_destination_line} * {32'd0,in_line_repeat};
        next_out_source_address = bad ? 0 : 64'(in_source_address);
        next_out_destination_address = bad ? 0 : 64'(in_destination_address);
        next_out_line = bad ? 0 : 27'((in_line_size >> 5) - 1);
        next_out_source_type = bad ? 0 : 1'(in_source_type);
        next_out_destination_type = bad ? 0 : 1'(in_destination_type);
        next_out_line_repeat = bad ? 0 : 13'(in_line_repeat - 1);
        next_out_source_line = bad ? 0 : 32'(in_source_line);
        next_out_destination_line = bad ? 0 : 32'(in_destination_line);
        next_out_surface_repeat = bad ? 0 : 13'(in_surface_repeat - 1);
        next_out_source_surface = bad ? 0 : 32'(in_source_surface);
        next_out_destination_surface = bad ? 0 : 32'(in_destination_surface);
        next_out_enable = bad ? 0 : 1'(1);
        next_out_illegal = bad;
    end

    assign in_ready = !out_valid || out_ready;
    always_ff @(posedge clk) begin
        if (rst) begin out_valid <= 0; out_source_address <= 0; out_destination_address <= 0; out_line <= 0; out_source_type <= 0; out_destination_type <= 0; out_line_repeat <= 0; out_source_line <= 0; out_destination_line <= 0; out_surface_repeat <= 0; out_source_surface <= 0; out_destination_surface <= 0; out_enable <= 0; out_illegal <= 0; end
        else if (in_ready) begin
            out_valid <= in_valid;
            if (in_valid) begin out_source_address <= next_out_source_address; out_destination_address <= next_out_destination_address; out_line <= next_out_line; out_source_type <= next_out_source_type; out_destination_type <= next_out_destination_type; out_line_repeat <= next_out_line_repeat; out_source_line <= next_out_source_line; out_destination_line <= next_out_destination_line; out_surface_repeat <= next_out_surface_repeat; out_source_surface <= next_out_source_surface; out_destination_surface <= next_out_destination_surface; out_enable <= next_out_enable; out_illegal <= next_out_illegal; end
            else begin out_source_address <= 0; out_destination_address <= 0; out_line <= 0; out_source_type <= 0; out_destination_type <= 0; out_line_repeat <= 0; out_source_line <= 0; out_destination_line <= 0; out_surface_repeat <= 0; out_source_surface <= 0; out_destination_surface <= 0; out_enable <= 0; out_illegal <= 0; end
        end
    end
endmodule
