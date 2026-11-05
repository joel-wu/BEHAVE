module compression_jpeg_entropy_jpeg_huffman_emit_step (
    input  logic         clk,
    input  logic         rst,

    input  logic         in_valid,
    output logic         in_ready,
    input  logic [63:0]  in_code,
    input  logic [6:0]   in_size,

    output logic         out_valid,
    input  logic         out_ready,
    output logic [7:0]   out_byte
);

    logic [63:0] put_buffer;
    logic [6:0]  free_bits;

    logic        busy;
    logic [4:0]  out_count;
    logic [3:0]  out_index;
    logic [127:0] out_buf;

    logic [6:0]  size_eff;
    logic [63:0] code_masked;
    logic [6:0]  spill_bits;
    logic [63:0] flush_word;
    logic [127:0] next_out_buf;
    logic [4:0]  next_out_count;
    logic [63:0] next_put_buffer;
    logic [6:0]  next_free_bits;

    logic [7:0] cur_byte;

    wire input_accepted;
    wire output_consumed;
    wire frame_complete;

    assign input_accepted  = in_valid & in_ready;
    assign output_consumed = out_valid & out_ready;
    assign frame_complete  = output_consumed & (out_index == out_count - 1'b1);

    assign in_ready  = ~busy | frame_complete;
    assign out_valid = busy;
    assign out_byte  = cur_byte;

    always_comb begin
        if (in_size <= 7'd16) begin
            size_eff = in_size;
        end else begin
            size_eff = 7'd16;
        end

        if (size_eff == 7'd0) begin
            code_masked = 64'd0;
        end else begin
            code_masked = in_code & (64'hFFFF_FFFF_FFFF_FFFF >> (64 - size_eff));
        end
    end

    always_comb begin
        next_out_buf     = 128'd0;
        next_out_count   = 5'd0;
        next_put_buffer  = put_buffer;
        next_free_bits   = free_bits;
        spill_bits       = 7'd0;
        flush_word       = 64'd0;

        if (size_eff != 7'd0) begin
            if (size_eff <= free_bits) begin
                next_put_buffer = (put_buffer << size_eff) | code_masked;
                next_free_bits  = free_bits - size_eff;
            end else begin
                spill_bits      = size_eff - free_bits;
                flush_word      = (put_buffer << free_bits) | (code_masked >> spill_bits);
                next_put_buffer = code_masked;
                next_free_bits  = 7'd64 - spill_bits;

                next_out_count = 5'd0;

                next_out_buf[127 - (next_out_count*8) -: 8] = flush_word[63:56];
                next_out_count = next_out_count + 5'd1;
                if (flush_word[63:56] == 8'hFF) begin
                    next_out_buf[127 - (next_out_count*8) -: 8] = 8'h00;
                    next_out_count = next_out_count + 5'd1;
                end

                next_out_buf[127 - (next_out_count*8) -: 8] = flush_word[55:48];
                next_out_count = next_out_count + 5'd1;
                if (flush_word[55:48] == 8'hFF) begin
                    next_out_buf[127 - (next_out_count*8) -: 8] = 8'h00;
                    next_out_count = next_out_count + 5'd1;
                end

                next_out_buf[127 - (next_out_count*8) -: 8] = flush_word[47:40];
                next_out_count = next_out_count + 5'd1;
                if (flush_word[47:40] == 8'hFF) begin
                    next_out_buf[127 - (next_out_count*8) -: 8] = 8'h00;
                    next_out_count = next_out_count + 5'd1;
                end

                next_out_buf[127 - (next_out_count*8) -: 8] = flush_word[39:32];
                next_out_count = next_out_count + 5'd1;
                if (flush_word[39:32] == 8'hFF) begin
                    next_out_buf[127 - (next_out_count*8) -: 8] = 8'h00;
                    next_out_count = next_out_count + 5'd1;
                end

                next_out_buf[127 - (next_out_count*8) -: 8] = flush_word[31:24];
                next_out_count = next_out_count + 5'd1;
                if (flush_word[31:24] == 8'hFF) begin
                    next_out_buf[127 - (next_out_count*8) -: 8] = 8'h00;
                    next_out_count = next_out_count + 5'd1;
                end

                next_out_buf[127 - (next_out_count*8) -: 8] = flush_word[23:16];
                next_out_count = next_out_count + 5'd1;
                if (flush_word[23:16] == 8'hFF) begin
                    next_out_buf[127 - (next_out_count*8) -: 8] = 8'h00;
                    next_out_count = next_out_count + 5'd1;
                end

                next_out_buf[127 - (next_out_count*8) -: 8] = flush_word[15:8];
                next_out_count = next_out_count + 5'd1;
                if (flush_word[15:8] == 8'hFF) begin
                    next_out_buf[127 - (next_out_count*8) -: 8] = 8'h00;
                    next_out_count = next_out_count + 5'd1;
                end

                next_out_buf[127 - (next_out_count*8) -: 8] = flush_word[7:0];
                next_out_count = next_out_count + 5'd1;
                if (flush_word[7:0] == 8'hFF) begin
                    next_out_buf[127 - (next_out_count*8) -: 8] = 8'h00;
                    next_out_count = next_out_count + 5'd1;
                end
            end
        end
    end

    always_comb begin
        cur_byte = 8'h00;
        if (busy) begin
            unique case (out_index)
                4'd0:  cur_byte = out_buf[127:120];
                4'd1:  cur_byte = out_buf[119:112];
                4'd2:  cur_byte = out_buf[111:104];
                4'd3:  cur_byte = out_buf[103:96];
                4'd4:  cur_byte = out_buf[95:88];
                4'd5:  cur_byte = out_buf[87:80];
                4'd6:  cur_byte = out_buf[79:72];
                4'd7:  cur_byte = out_buf[71:64];
                4'd8:  cur_byte = out_buf[63:56];
                4'd9:  cur_byte = out_buf[55:48];
                4'd10: cur_byte = out_buf[47:40];
                4'd11: cur_byte = out_buf[39:32];
                4'd12: cur_byte = out_buf[31:24];
                4'd13: cur_byte = out_buf[23:16];
                4'd14: cur_byte = out_buf[15:8];
                4'd15: cur_byte = out_buf[7:0];
            endcase
        end
    end

    always_ff @(posedge clk) begin
        if (rst) begin
            put_buffer <= 64'h0000_0000_0000_0000;
            free_bits  <= 7'd64;
            busy       <= 1'b0;
            out_count  <= 5'd0;
            out_index  <= 4'd0;
            out_buf    <= 128'd0;
        end else if (input_accepted) begin
            put_buffer <= next_put_buffer;
            free_bits  <= next_free_bits;

            if ((size_eff != 7'd0) && (size_eff > free_bits)) begin
                busy      <= 1'b1;
                out_count <= next_out_count;
                out_index <= 4'd0;
                out_buf   <= next_out_buf;
            end else begin
                busy      <= 1'b0;
                out_count <= 5'd0;
                out_index <= 4'd0;
                out_buf   <= 128'd0;
            end
        end else if (output_consumed) begin
            if (out_index == out_count - 1'b1) begin
                busy      <= 1'b0;
                out_count <= 5'd0;
                out_index <= 4'd0;
                out_buf   <= 128'd0;
            end else begin
                busy      <= 1'b1;
                out_index <= out_index + 4'd1;
            end
        end
    end

endmodule
